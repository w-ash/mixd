#!/usr/bin/env python3
"""Read-only integrity checks over imported play data.

A re-runnable check registry, seeded in v0.10.1 with the calibration query that
decides whether Spotify's recently-played ``played_at`` marks the START or the
END of a play. v0.10.3's data-integrity audit extends this registry rather than
writing one-off psql sessions, so every check survives to be re-run after the
next import or connector.

Every check is a SELECT. Nothing here mutates.

Usage:
    uv run python scripts/audit_play_integrity.py
    uv run python scripts/audit_play_integrity.py --check spotify_api_lastfm_delta
    uv run python scripts/audit_play_integrity.py --list
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from decimal import Decimal
import statistics
import sys

from sqlalchemy import text
from sqlalchemy.engine.row import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import setup_script_logger
from src.infrastructure.persistence.database import get_session

# Pair an API observation with the nearest Last.fm scrobble of the same listen.
# ±10 min is deliberately far wider than any plausible clock skew: the point is
# to MEASURE the offset distribution, so a window narrower than the effect being
# measured would beg the question.
_PAIR_WINDOW_SECONDS = 600

# Findings §3 buckets. |Δ| ≤ 5s means the channels agree on the start; a cluster
# near +duration_ms is the END-semantics signature.
_TIGHT_AGREEMENT_SECONDS = 5
_TIGHT_TOLERANCE_SECONDS = 30
_FALLBACK_TOLERANCE_SECONDS = 180

_EXEMPLAR_LIMIT = 5

# Last.fm refuses to scrobble a track shorter than this, so a play on one can
# never have a Last.fm twin. ``should_include_spotify_play`` deliberately has no
# such floor (its docstring says why), which makes those plays a *structural*
# blind spot in cross-channel pairing, not a set of misses.
_LASTFM_SCROBBLE_FLOOR_MS = 30_000

# Pairing tolerance for export↔Last.fm: the same 30s the convergence findings
# used, so the rate here is comparable to the ~82.7% baseline recorded there.
_CROSS_CHANNEL_TOLERANCE_SECONDS = 30

# Two discards closer together than this belong to the same sitting. Chosen to
# be shorter than any plausible deliberate listen, so the fraction below it
# reads as "still clicking", not "played something and came back".
_BURST_GAP_SECONDS = 60


@dataclass(frozen=True)
class CheckResult:
    """One check's verdict, with the evidence needed to act on it."""

    name: str
    verdict: str
    count: int
    query: str
    exemplars: list[dict[str, object]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# Pairs each spotify_api play with its nearest Last.fm neighbour for the same
# track. Falls back to exact-normalized artist::title when resolution diverged,
# so an identity defect doesn't silently shrink the calibration sample.
_DELTA_QUERY = """
WITH api AS (
    SELECT cp.id, cp.user_id, cp.played_at, cp.resolved_track_id,
           lower(cp.raw_metadata ->> 'artist_name') AS artist_key,
           lower(cp.raw_metadata ->> 'track_name') AS track_key,
           (cp.raw_metadata -> 'service_metadata' ->> 'duration_ms')::bigint
               AS duration_ms
    FROM connector_plays cp
    WHERE cp.import_source = 'spotify_api'
),
scrobble AS (
    SELECT cp.id, cp.user_id, cp.played_at, cp.resolved_track_id,
           lower(cp.raw_metadata ->> 'artist_name') AS artist_key,
           lower(cp.raw_metadata ->> 'track_name') AS track_key
    FROM connector_plays cp
    WHERE cp.import_source = 'lastfm_api'
)
SELECT
    api.id AS api_id,
    api.played_at AS api_played_at,
    scrobble.played_at AS lastfm_played_at,
    api.duration_ms,
    EXTRACT(EPOCH FROM (scrobble.played_at - api.played_at)) AS delta_seconds
FROM api
LEFT JOIN LATERAL (
    SELECT s.*
    FROM scrobble s
    WHERE s.user_id = api.user_id
      AND (
          (s.resolved_track_id IS NOT NULL
           AND s.resolved_track_id = api.resolved_track_id)
          OR (s.artist_key = api.artist_key AND s.track_key = api.track_key)
      )
      AND s.played_at BETWEEN api.played_at - make_interval(secs => :window)
                          AND api.played_at + make_interval(secs => :window)
    ORDER BY abs(EXTRACT(EPOCH FROM (s.played_at - api.played_at)))
    LIMIT 1
) AS scrobble ON TRUE
ORDER BY api.played_at DESC
"""

# Scrobbles with no API twin. Reported as a blind spot, never as misses: the
# recently-played endpoint records only plays over 30s and omits private
# sessions, so a permanent unpaired residue is expected (findings §7: 28.2% of
# GDPR rows are under 30s).
_BLIND_SPOT_QUERY = """
SELECT count(*) AS unpaired_scrobbles
FROM connector_plays lf
WHERE lf.import_source = 'lastfm_api'
  AND lf.played_at >= (
      SELECT min(played_at) FROM connector_plays WHERE import_source = 'spotify_api'
  )
  AND NOT EXISTS (
      SELECT 1 FROM connector_plays api
      WHERE api.import_source = 'spotify_api'
        AND api.user_id = lf.user_id
        AND api.played_at BETWEEN lf.played_at - make_interval(secs => :window)
                              AND lf.played_at + make_interval(secs => :window)
  )
"""


_CENSUS_COUNTS_QUERY = """
SELECT 'tracks' AS table_name, count(*) AS row_count FROM tracks
UNION ALL SELECT 'connector_tracks', count(*) FROM connector_tracks
UNION ALL SELECT 'track_mappings', count(*) FROM track_mappings
UNION ALL SELECT 'match_reviews', count(*) FROM match_reviews
UNION ALL SELECT 'resolution_events', count(*) FROM resolution_events
UNION ALL SELECT 'resolution_negatives', count(*) FROM resolution_negatives
UNION ALL SELECT 'track_likes', count(*) FROM track_likes
UNION ALL SELECT 'track_plays', count(*) FROM track_plays
UNION ALL SELECT 'connector_plays', count(*) FROM connector_plays
UNION ALL SELECT 'play_sources', count(*) FROM play_sources
UNION ALL SELECT 'playlists', count(*) FROM playlists
UNION ALL SELECT 'connector_playlists', count(*) FROM connector_playlists
UNION ALL SELECT 'playlist_mappings', count(*) FROM playlist_mappings
UNION ALL SELECT 'playlist_tracks', count(*) FROM playlist_tracks
UNION ALL SELECT 'sync_checkpoints', count(*) FROM sync_checkpoints
UNION ALL SELECT 'track_preferences', count(*) FROM track_preferences
ORDER BY table_name
"""

# The tables the audit's tenancy checks (group D) compare against; a census
# row with user_id 'default' on a remote database is the mistenancy class.
_CENSUS_TENANCY_QUERY = """
SELECT 'tracks' AS table_name, user_id, count(*) AS row_count
FROM tracks GROUP BY user_id
UNION ALL SELECT 'track_mappings', user_id, count(*)
FROM track_mappings GROUP BY user_id
UNION ALL SELECT 'track_likes', user_id, count(*)
FROM track_likes GROUP BY user_id
UNION ALL SELECT 'track_plays', user_id, count(*)
FROM track_plays GROUP BY user_id
UNION ALL SELECT 'connector_plays', user_id, count(*)
FROM connector_plays GROUP BY user_id
ORDER BY table_name, user_id
"""

_CENSUS_CONNECTOR_PLAYS_QUERY = """
SELECT import_source, count(*) AS row_count,
       min(played_at) AS first_played_at, max(played_at) AS last_played_at
FROM connector_plays
GROUP BY import_source
ORDER BY import_source
"""

_CENSUS_TRACK_PLAYS_QUERY = """
SELECT count(*) AS row_count,
       min(played_at) AS first_played_at, max(played_at) AS last_played_at
FROM track_plays
"""

_CENSUS_MAPPING_LIFECYCLE_QUERY = """
SELECT count(*) FILTER (WHERE superseded_at IS NULL) AS live,
       count(*) FILTER (WHERE superseded_at IS NOT NULL) AS superseded
FROM track_mappings
"""

_CENSUS_MIGRATION_QUERY = "SELECT version_num FROM alembic_version"


@dataclass(frozen=True)
class _DeltaRow:
    """One api↔scrobble pairing, narrowed out of the driver's untyped mapping."""

    api_played_at: object
    lastfm_played_at: object
    delta_seconds: float | None
    duration_seconds: float | None

    @classmethod
    def from_row(cls, row: RowMapping) -> _DeltaRow:
        """Narrow one driver row; RowMapping.get() is untyped by design."""
        values: dict[str, object] = dict(row)
        return cls(
            api_played_at=values.get("api_played_at"),
            lastfm_played_at=values.get("lastfm_played_at"),
            delta_seconds=_as_float(values.get("delta_seconds")),
            duration_seconds=_as_millis_in_seconds(values.get("duration_ms")),
        )


def _as_float(value: object) -> float | None:
    """Narrow a numeric column value; Postgres returns Decimal for EXTRACT."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float | Decimal):
        return float(value)
    return None


def _as_millis_in_seconds(value: object) -> float | None:
    millis = _as_float(value)
    return None if millis is None else millis / 1000


def _bucket(delta: float) -> str:
    """Findings §3 bucket for one signed offset, in seconds."""
    magnitude = abs(delta)
    if magnitude <= _TIGHT_AGREEMENT_SECONDS:
        return "agree (|d| <= 5s)"
    if magnitude <= _TIGHT_TOLERANCE_SECONDS:
        return "tight (5-30s)"
    if magnitude <= _FALLBACK_TOLERANCE_SECONDS:
        return "fallback (30-180s)"
    return "beyond fallback (>180s)"


async def check_spotify_api_lastfm_delta(session: AsyncSession) -> CheckResult:
    """Calibrate spotify_api played_at semantics against Last.fm scrobbles.

    **Settled 2026-08-09: the API stamps the END of a play.** This check is the
    one that answered it — median ``lastfm - api`` offset -205.8s against a
    219.7s median track duration, corroborated by a 90/90 one-directional sign
    test and by consecutive-stamp gap analysis. ``CHANNEL_SPECS`` now declares
    ``time_semantics="end"`` for the channel and the ``tolerance_override`` is
    gone; the correction rides as ledger-carried ``start_shift_ms``.

    Retained as a regression guard rather than an open question. Read the
    median: it should now sit near 0 for stamped rows. A drift back toward
    +duration_ms means the importer stopped stamping the shift — the 113 rows
    imported before that landed are the known unstamped residue and still show
    the old offset until they are backfilled.
    """
    raw = (
        (await session.execute(text(_DELTA_QUERY), {"window": _PAIR_WINDOW_SECONDS}))
        .mappings()
        .all()
    )
    rows = [_DeltaRow.from_row(m) for m in raw]
    if not rows:
        return CheckResult(
            name="spotify_api_lastfm_delta",
            verdict="NO DATA",
            count=0,
            query=_DELTA_QUERY,
            notes=[
                (
                    "No spotify_api rows yet — run a recently-played import first "
                    "(`mixd history import-spotify-recent`)."
                )
            ],
        )

    deltas = [r.delta_seconds for r in rows if r.delta_seconds is not None]
    paired = [r for r in rows if r.delta_seconds is not None]
    buckets: dict[str, int] = {}
    for delta in deltas:
        key = _bucket(delta)
        buckets[key] = buckets.get(key, 0) + 1
    buckets["unpaired"] = len(rows) - len(deltas)

    notes = [f"{len(rows)} spotify_api plays, {len(deltas)} paired within 10 min"]
    notes += [f"  {name}: {count}" for name, count in sorted(buckets.items())]

    if deltas:
        median = statistics.median(deltas)
        notes.append(f"median offset (lastfm - api): {median:+.1f}s")

        durations = [
            r.duration_seconds for r in paired if r.duration_seconds is not None
        ]
        if durations:
            median_duration = statistics.median(durations)
            notes.append(f"median track duration: {median_duration:.1f}s")
            notes.append(
                "SEMANTICS: "
                + (
                    "start-aligned — the END-stamp correction is being applied"
                    if abs(median) <= _TIGHT_AGREEMENT_SECONDS
                    else f"NOT start-aligned; compare |{median:+.1f}s| against "
                    f"the {median_duration:.1f}s duration for an END signature. "
                    f"Expected while unstamped pre-fix rows dominate the sample; "
                    f"a regression otherwise"
                )
            )

    # Same narrowing route as the delta rows: scalar_one() is untyped, while a
    # dict() of the mapping declares its values as object.
    blind_rows = (
        (
            await session.execute(
                text(_BLIND_SPOT_QUERY), {"window": _PAIR_WINDOW_SECONDS}
            )
        )
        .mappings()
        .all()
    )
    blind_values: dict[str, object] = dict(blind_rows[0]) if blind_rows else {}
    blind = _as_float(blind_values.get("unpaired_scrobbles")) or 0
    notes.append(
        f"blind spot: {blind:.0f} scrobbles in the polled window with no API twin "
        "(expected — the API skips plays under 30s and private sessions)"
    )

    return CheckResult(
        name="spotify_api_lastfm_delta",
        verdict="REVIEW" if deltas else "NO PAIRS",
        count=len(deltas),
        query=_DELTA_QUERY,
        exemplars=[
            {
                "api_played_at": r.api_played_at,
                "lastfm_played_at": r.lastfm_played_at,
                "delta_seconds": r.delta_seconds,
                "duration_seconds": r.duration_seconds,
            }
            for r in paired[:_EXEMPLAR_LIMIT]
        ],
        notes=notes,
    )


def _as_int(value: object) -> int:
    narrowed = _as_float(value)
    return 0 if narrowed is None else int(narrowed)


def _query_over(population_cte: str, body: str) -> str:
    """One query = a shared population CTE + the body that reads it.

    Several checks must read *exactly* the same rows to be comparable — a
    discard-shape query and a discard-cadence query disagreeing on the
    population would produce two incompatible pictures of one number. Naming
    the CTE once and composing here is what keeps them honest. Both arguments
    are module constants; every runtime value travels as a bound parameter, so
    nothing external reaches the SQL text.
    """
    return population_cte + body


# What the listen threshold actually discards. The rule (play_resolver.py:
# ``should_include_spotify_play``) counts a play when it ran >= 4 minutes, or
# reached >= 50% of a track under 8 minutes; a service export is an event log,
# so it discards a large share of rows by design. This reports the shape of
# that discard — and what alternative thresholds would admit — so the policy is
# chosen against the user's own listening rather than a guess.
#
# Scoped to plays whose track resolved and was then dropped: an unidentifiable
# play says nothing about the threshold. Incognito is excluded for the same
# reason (a private session is out regardless of length). The ledger stores the
# full spotify:track: URI while connector_tracks stores the bare id.
_THRESHOLD_DISCARD_CTE = """
WITH discarded AS (
    SELECT cp.id,
           cp.ms_played,
           cp.played_at,
           ct.duration_ms,
           cp.raw_metadata -> 'service_metadata' ->> 'reason_end' AS reason_end,
           cp.raw_metadata -> 'service_metadata' ->> 'reason_start' AS reason_start
    FROM connector_plays cp
    JOIN connector_tracks ct
      ON ct.connector_name = cp.connector_name
     AND ct.connector_track_identifier = regexp_replace(
             cp.connector_track_identifier, '^spotify:track:', ''
         )
    WHERE cp.resolved_track_id IS NULL
      AND cp.ms_played IS NOT NULL
      AND coalesce(
              cp.raw_metadata -> 'service_metadata' ->> 'incognito_mode', 'false'
          ) <> 'true'
      AND EXISTS (
          SELECT 1 FROM track_mappings tm
          WHERE tm.connector_track_id = ct.id
            AND tm.user_id = cp.user_id
            AND tm.superseded_at IS NULL
      )
)
"""

_DISCARD_BY_REASON_QUERY = _query_over(
    _THRESHOLD_DISCARD_CTE,
    """
SELECT reason_end,
       count(*) AS n,
       round(avg(ms_played) / 1000.0) AS avg_seconds,
       round(100.0 * avg(
           CASE WHEN duration_ms > 0
                THEN ms_played::numeric / duration_ms END
       )) AS avg_pct_of_track
FROM discarded
GROUP BY reason_end
ORDER BY n DESC
""",
)

_DISCARD_BY_FRACTION_QUERY = _query_over(
    _THRESHOLD_DISCARD_CTE,
    """
SELECT CASE
         WHEN duration_ms IS NULL OR duration_ms = 0 THEN 'unknown'
         WHEN ms_played::numeric / duration_ms < 0.05 THEN 'a. <5%'
         WHEN ms_played::numeric / duration_ms < 0.15 THEN 'b. 5-15%'
         WHEN ms_played::numeric / duration_ms < 0.25 THEN 'c. 15-25%'
         WHEN ms_played::numeric / duration_ms < 0.35 THEN 'd. 25-35%'
         ELSE 'e. 35-50%' END AS fraction_band,
       count(*) AS n
FROM discarded
GROUP BY 1 ORDER BY 1
""",
)

# The decision table: how many currently-discarded plays each candidate rule
# would admit. Reported as counts the user can weigh, never applied here.
_DISCARD_ALTERNATIVES_QUERY = _query_over(
    _THRESHOLD_DISCARD_CTE,
    """
SELECT count(*) AS discarded_today,
       count(*) FILTER (WHERE ms_played >= 30000) AS would_admit_30s_floor,
       count(*) FILTER (
           WHERE duration_ms > 0 AND ms_played::numeric / duration_ms >= 0.25
       ) AS would_admit_25_pct,
       count(*) FILTER (
           WHERE duration_ms > 0 AND ms_played::numeric / duration_ms >= 0.33
       ) AS would_admit_33_pct,
       count(*) FILTER (WHERE reason_end = 'trackdone') AS ran_to_completion
FROM discarded
""",
)

_DISCARD_BY_YEAR_QUERY = _query_over(
    _THRESHOLD_DISCARD_CTE,
    """
SELECT date_part('year', played_at) AS year, count(*) AS n
FROM discarded
GROUP BY 1 ORDER BY 1
""",
)


async def check_listen_threshold_discards(session: AsyncSession) -> CheckResult:
    """Shape of what the listen threshold discards, and what alternatives admit.

    Report-only, and deliberately so: where the threshold belongs is the
    user's call, not the audit's. What the audit can supply is the cost of the
    current setting measured against real listening — including the plays that
    ran to completion and were discarded anyway, which are the ones most likely
    to feel wrong to the person whose history it is.
    """
    by_reason = await _census_rows(session, _DISCARD_BY_REASON_QUERY)
    by_fraction = await _census_rows(session, _DISCARD_BY_FRACTION_QUERY)
    alternatives = await _census_rows(session, _DISCARD_ALTERNATIVES_QUERY)
    by_year = await _census_rows(session, _DISCARD_BY_YEAR_QUERY)

    total = _as_int(alternatives[0]["discarded_today"]) if alternatives else 0

    notes = [f"resolved-then-discarded plays: {total}"]

    if alternatives:
        row = alternatives[0]
        notes.append("would be admitted by an alternative threshold:")
        for key, label in (
            ("would_admit_30s_floor", "30s absolute floor (Spotify's own rule)"),
            ("would_admit_25_pct", ">=25% of track"),
            ("would_admit_33_pct", ">=33% of track"),
        ):
            count = _as_int(row[key])
            share = f"{100.0 * count / total:.1f}%" if total else "-"
            notes.append(f"  {label}: {count} ({share} of discards)")
        notes.append(
            f"  discarded despite reason_end='trackdone': "
            f"{_as_int(row['ran_to_completion'])}"
        )

    notes.append("by reason_end:")
    notes += [
        f"  {r['reason_end']}: {_as_int(r['n'])} "
        f"(avg {_as_int(r['avg_seconds'])}s, {_as_int(r['avg_pct_of_track'])}% of track)"
        for r in by_reason[:10]
    ]

    notes.append("by fraction of track reached:")
    notes += [f"  {r['fraction_band']}: {_as_int(r['n'])}" for r in by_fraction]

    notes.append("by year:")
    notes += [f"  {_as_int(r['year'])}: {_as_int(r['n'])}" for r in by_year]

    return CheckResult(
        name="listen_threshold_discards",
        verdict="INFO",
        count=total,
        query=_DISCARD_ALTERNATIVES_QUERY,
        notes=notes,
    )


# Is a five-figure discard count a broken threshold, or an event log doing what
# an event log does? Averaged over calendar days it looks like the former (~15/day
# for fifteen years, which nobody listens like). The distribution answers it:
# discards are not spread, they arrive in bursts on a minority of days.
#
# ``percentile_cont`` over the per-active-day counts rather than a mean, because
# the mean is dragged by exactly the burst days whose existence is the question.
_DISCARD_CADENCE_QUERY = _query_over(
    _THRESHOLD_DISCARD_CTE,
    """
, per_day AS (
    SELECT (played_at AT TIME ZONE 'UTC')::date AS active_day, count(*) AS n
    FROM discarded
    GROUP BY 1
),
day_stats AS (
    SELECT sum(n) AS total,
           count(*) AS active_days,
           round(avg(n), 1) AS mean_per_day,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY n) AS median_per_day,
           percentile_cont(0.9) WITHIN GROUP (ORDER BY n) AS p90_per_day,
           max(n) AS max_per_day
    FROM per_day
),
gaps AS (
    SELECT EXTRACT(EPOCH FROM (
               played_at - lag(played_at) OVER (ORDER BY played_at)
           )) AS gap_seconds
    FROM discarded
),
gap_stats AS (
    SELECT count(*) FILTER (WHERE gap_seconds <= :burst_gap) AS in_burst,
           count(*) AS gap_total
    FROM gaps
    WHERE gap_seconds IS NOT NULL
)
SELECT day_stats.*, gap_stats.*
FROM day_stats, gap_stats
""",
)

# ``reason_start`` is the behavioural half of the answer: it says how the play
# was begun, so a discard mix dominated by 'clickrow' is a person clicking down
# a list sampling tracks — crate-digging, which SHOULD discard nearly all of it.
_DISCARD_BY_REASON_START_QUERY = _query_over(
    _THRESHOLD_DISCARD_CTE,
    """
SELECT coalesce(nullif(reason_start, ''), '(none)') AS reason_start,
       count(*) AS n,
       round(avg(ms_played) / 1000.0, 1) AS avg_seconds
FROM discarded
GROUP BY 1 ORDER BY n DESC LIMIT 8
""",
)

# The heaviest days, with their clickrow share and span — the concrete shape a
# reader needs to accept the distribution above as a session rather than a leak.
_DISCARD_BURST_DAYS_QUERY = _query_over(
    _THRESHOLD_DISCARD_CTE,
    """
SELECT (played_at AT TIME ZONE 'UTC')::date AS active_day,
       count(*) AS n,
       count(*) FILTER (WHERE reason_start = 'clickrow') AS clickrow,
       round(avg(ms_played) / 1000.0, 1) AS avg_seconds,
       min(played_at)::time(0) AS first_at,
       max(played_at)::time(0) AS last_at
FROM discarded
GROUP BY 1 ORDER BY n DESC LIMIT 5
""",
)


async def check_discard_plausibility(session: AsyncSession) -> CheckResult:
    """Tell crate-digging from threshold inflation in the discard count.

    The audit's discard total gets read as a per-day average and disbelieved,
    then re-derived by hand. This is the derivation, made re-runnable: the
    per-active-day distribution, how much of the total lands inside a burst,
    and the ``reason_start`` mix. A large discard count is *expected* for an
    event log, and these three numbers are how you tell that from a threshold
    quietly eating real listens.
    """
    cadence = await _census_rows(
        session, _DISCARD_CADENCE_QUERY, {"burst_gap": _BURST_GAP_SECONDS}
    )
    by_reason_start = await _census_rows(session, _DISCARD_BY_REASON_START_QUERY)
    burst_days = await _census_rows(session, _DISCARD_BURST_DAYS_QUERY)

    row = cadence[0] if cadence else {}
    total = _as_int(row.get("total"))
    if not total:
        return CheckResult(
            name="discard_plausibility",
            verdict="NO DATA",
            count=0,
            query=_DISCARD_CADENCE_QUERY,
            notes=["No resolved-then-discarded plays — nothing to characterise."],
        )

    active_days = _as_int(row.get("active_days"))
    gap_total = _as_int(row.get("gap_total"))
    in_burst = _as_int(row.get("in_burst"))
    burst_share = 100.0 * in_burst / gap_total if gap_total else 0.0

    notes = [
        f"resolved-then-discarded plays: {total} over {active_days} active days",
        (
            f"per active day: median {_as_float(row.get('median_per_day')) or 0:.0f}, "
            f"mean {_as_float(row.get('mean_per_day')) or 0:.1f}, "
            f"p90 {_as_float(row.get('p90_per_day')) or 0:.1f}, "
            f"max {_as_int(row.get('max_per_day'))}"
        ),
        (
            f"burstiness: {burst_share:.1f}% of discards land within "
            f"{_BURST_GAP_SECONDS}s of the previous one ({in_burst}/{gap_total})"
        ),
    ]

    notes.append("by reason_start (how the play was begun):")
    notes += [
        f"  {r['reason_start']}: {_as_int(r['n'])} "
        f"({100.0 * _as_int(r['n']) / total:.1f}%, avg "
        f"{_as_float(r['avg_seconds']) or 0:.0f}s)"
        for r in by_reason_start
    ]

    notes.append("heaviest days:")
    notes += [
        f"  {r['active_day']}: {_as_int(r['n'])} discards "
        f"({_as_int(r['clickrow'])} clickrow, avg "
        f"{_as_float(r['avg_seconds']) or 0:.0f}s, "
        f"{r['first_at']}..{r['last_at']})"
        for r in burst_days
    ]

    notes.append(
        "READ: a median active day in single digits with a long tail, most "
        "discards inside a burst, and a clickrow-dominated mix is crate-digging "
        "— the threshold discarding what it should. A high median with a flat "
        "gap distribution would be the inflation case."
    )

    return CheckResult(
        name="discard_plausibility",
        verdict="INFO",
        count=total,
        query=_DISCARD_CADENCE_QUERY,
        notes=notes,
    )


# Export plays that COUNTED, on tracks shorter than Last.fm's scrobble floor.
# The join note: the ledger stores the full spotify:track: URI, connector_tracks
# the bare id. duration_ms = 0 is "unknown", not "short", so it stays out.
_SHORT_TRACK_CTE = """
WITH short_plays AS (
    SELECT cp.id,
           cp.user_id,
           cp.played_at,
           cp.ms_played,
           cp.resolved_track_id,
           ct.duration_ms,
           ct.title,
           ct.artists -> 'names' ->> 0 AS artist_name,
           lower(cp.raw_metadata ->> 'artist_name') AS artist_key,
           lower(cp.raw_metadata ->> 'track_name') AS track_key
    FROM connector_plays cp
    JOIN connector_tracks ct
      ON ct.connector_name = cp.connector_name
     AND ct.connector_track_identifier = regexp_replace(
             cp.connector_track_identifier, '^spotify:track:', ''
         )
    WHERE cp.import_source = 'spotify_export'
      AND cp.resolved_track_id IS NOT NULL
      AND ct.duration_ms > 0
      AND ct.duration_ms <= :floor_ms
)
"""

_SHORT_TRACK_CENSUS_QUERY = _query_over(
    _SHORT_TRACK_CTE,
    """
SELECT count(*) AS plays,
       count(DISTINCT resolved_track_id) AS tracks,
       round(avg(duration_ms) / 1000.0, 1) AS avg_duration_seconds,
       count(*) FILTER (WHERE ms_played >= duration_ms) AS played_in_full,
       count(*) FILTER (WHERE EXISTS (
           SELECT 1
           FROM connector_plays lf
           WHERE lf.import_source = 'lastfm_api'
             AND lf.user_id = short_plays.user_id
             AND ((lf.resolved_track_id IS NOT NULL
                   AND lf.resolved_track_id = short_plays.resolved_track_id)
                  OR (lower(lf.raw_metadata ->> 'artist_name')
                          = short_plays.artist_key
                      AND lower(lf.raw_metadata ->> 'track_name')
                          = short_plays.track_key))
             AND lf.played_at
                 BETWEEN short_plays.played_at - make_interval(secs => :window)
                     AND short_plays.played_at + make_interval(secs => :window)
       )) AS with_scrobble_nearby
FROM short_plays
""",
)

_SHORT_TRACK_EXEMPLARS_QUERY = _query_over(
    _SHORT_TRACK_CTE,
    """
SELECT artist_name, title,
       round(avg(duration_ms) / 1000.0, 1) AS duration_seconds,
       count(*) AS plays
FROM short_plays
GROUP BY 1, 2
ORDER BY plays DESC, duration_seconds
LIMIT 5
""",
)

# The pairing rate this bucket corrects. Scoped to calendar days that actually
# carry scrobbles — Last.fm coverage is a recent slice of a fifteen-year export,
# and a window spanning the gap would measure absent coverage, not pairing.
#
# Export ``played_at`` marks the END of a play (the audit re-derived this for
# 132,199 rows), so the start-aligned comparison subtracts ms_played — the same
# normalization the projection applies.
_SHORT_TRACK_PAIRING_QUERY = """
WITH scrobble AS (
    SELECT cp.user_id, cp.played_at, cp.resolved_track_id,
           lower(cp.raw_metadata ->> 'artist_name') AS artist_key,
           lower(cp.raw_metadata ->> 'track_name') AS track_key
    FROM connector_plays cp
    WHERE cp.import_source = 'lastfm_api'
),
covered_days AS (
    SELECT DISTINCT (played_at AT TIME ZONE 'UTC')::date AS covered_day
    FROM scrobble
),
export_play AS (
    SELECT cp.user_id,
           cp.resolved_track_id,
           cp.played_at - make_interval(secs => coalesce(cp.ms_played, 0) / 1000.0)
               AS started_at,
           lower(cp.raw_metadata ->> 'artist_name') AS artist_key,
           lower(cp.raw_metadata ->> 'track_name') AS track_key,
           (ct.duration_ms > 0 AND ct.duration_ms <= :floor_ms) AS is_blind_spot
    FROM connector_plays cp
    LEFT JOIN connector_tracks ct
      ON ct.connector_name = cp.connector_name
     AND ct.connector_track_identifier = regexp_replace(
             cp.connector_track_identifier, '^spotify:track:', ''
         )
    WHERE cp.import_source = 'spotify_export'
      AND cp.resolved_track_id IS NOT NULL
      AND (cp.played_at AT TIME ZONE 'UTC')::date IN (
          SELECT covered_day FROM covered_days
      )
)
SELECT count(*) AS export_plays,
       count(*) FILTER (WHERE is_blind_spot) AS blind_spot,
       count(*) FILTER (
           WHERE NOT coalesce(is_blind_spot, false)
             AND EXISTS (
                 SELECT 1 FROM scrobble s
                 WHERE s.user_id = export_play.user_id
                   AND ((s.resolved_track_id IS NOT NULL
                         AND s.resolved_track_id = export_play.resolved_track_id)
                        OR (s.artist_key = export_play.artist_key
                            AND s.track_key = export_play.track_key))
                   AND s.played_at
                       BETWEEN export_play.started_at
                               - make_interval(secs => :tolerance)
                           AND export_play.started_at
                               + make_interval(secs => :tolerance)
             )
       ) AS paired
FROM export_play
"""


async def check_short_track_blind_spot(session: AsyncSession) -> CheckResult:
    """Bucket plays that no Last.fm scrobble could ever pair with.

    Last.fm will not scrobble a track under 30 seconds; the listen threshold
    (``should_include_spotify_play``) deliberately keeps no such floor, so a
    fully-played interlude counts here and is invisible to Last.fm by
    construction. Counting those as cross-channel misses is the pairing check
    measuring its own expectation, exactly as counting sub-30s scrobbles
    against the recently-played API would be — the same treatment that check
    already gives its own blind spot, one floor up.

    Report-only, and the bucket is the finding: the number to watch is that it
    stays small and stays *unpairable*. A short-track play that DID pair would
    mean the floor assumption is wrong, not that the blind spot closed.
    """
    census = await _census_rows(
        session,
        _SHORT_TRACK_CENSUS_QUERY,
        {"floor_ms": _LASTFM_SCROBBLE_FLOOR_MS, "window": _PAIR_WINDOW_SECONDS},
    )
    exemplars = await _census_rows(
        session, _SHORT_TRACK_EXEMPLARS_QUERY, {"floor_ms": _LASTFM_SCROBBLE_FLOOR_MS}
    )
    pairing = await _census_rows(
        session,
        _SHORT_TRACK_PAIRING_QUERY,
        {
            "floor_ms": _LASTFM_SCROBBLE_FLOOR_MS,
            "tolerance": _CROSS_CHANNEL_TOLERANCE_SECONDS,
        },
    )

    row = census[0] if census else {}
    plays = _as_int(row.get("plays"))
    nearby = _as_int(row.get("with_scrobble_nearby"))

    floor_seconds = _LASTFM_SCROBBLE_FLOOR_MS // 1000
    notes = [
        (
            f"structural blind spot: {plays} counted export plays on "
            f"{_as_int(row.get('tracks'))} canonical tracks under {floor_seconds}s "
            f"(avg {_as_float(row.get('avg_duration_seconds')) or 0:.1f}s, "
            f"{_as_int(row.get('played_in_full'))} played in full)"
        ),
        (
            f"  with a same-track scrobble within {_PAIR_WINDOW_SECONDS // 60} min: "
            f"{nearby} — Last.fm cannot scrobble a track this short, so these "
            "are unpairable by construction, not misses"
        ),
    ]
    if nearby:
        notes.append(
            "  ANOMALY: a short-track play paired anyway — re-check the "
            f"{floor_seconds}s floor assumption before trusting this bucket"
        )

    if exemplars:
        notes.append("exemplars:")
        notes += [
            f"  {r['artist_name']} — {r['title']} "
            f"({_as_float(r['duration_seconds']) or 0:.1f}s, {_as_int(r['plays'])} plays)"
            for r in exemplars
        ]

    pair_row = pairing[0] if pairing else {}
    export_plays = _as_int(pair_row.get("export_plays"))
    blind = _as_int(pair_row.get("blind_spot"))
    paired = _as_int(pair_row.get("paired"))
    denominator = export_plays - blind
    notes.append("export↔Last.fm pairing on days with scrobble coverage:")
    notes.append(f"  counted export plays on covered days: {export_plays}")
    notes.append(f"  excluded as structural blind spot: {blind}")
    if denominator:
        notes.append(
            f"  paired within {_CROSS_CHANNEL_TOLERANCE_SECONDS}s: {paired} of "
            f"{denominator} ({100.0 * paired / denominator:.1f}%)"
        )
        notes.append(f"  unpaired: {denominator - paired} (candidate misses)")
    else:
        notes.append("  no pairable export plays on a covered day — no rate to report")

    return CheckResult(
        name="short_track_blind_spot",
        verdict="ANOMALY" if nearby else "INFO",
        count=plays,
        query=_SHORT_TRACK_CENSUS_QUERY,
        notes=notes,
    )


async def _census_rows(
    session: AsyncSession, query: str, params: dict[str, object] | None = None
) -> list[dict[str, object]]:
    """One census query → plain dicts (RowMapping values are untyped)."""
    result = await session.execute(text(query), params or {})
    return [dict(m) for m in result.mappings().all()]


async def check_census(session: AsyncSession) -> CheckResult:
    """Row census across the audit's core tables.

    The before/after substrate for at-scale imports: run once against the
    frozen pre-import Neon branch, again after the import, and diff. Counts
    are cross-tenant whenever the connection role holds BYPASSRLS (Neon's
    ``neondb_owner`` does) — which is what a census wants; the per-user
    breakdown makes tenancy explicit instead of trusting RLS to scope it.
    """
    counts = await _census_rows(session, _CENSUS_COUNTS_QUERY)
    tenancy = await _census_rows(session, _CENSUS_TENANCY_QUERY)
    by_source = await _census_rows(session, _CENSUS_CONNECTOR_PLAYS_QUERY)
    play_range = await _census_rows(session, _CENSUS_TRACK_PLAYS_QUERY)
    lifecycle = await _census_rows(session, _CENSUS_MAPPING_LIFECYCLE_QUERY)
    migration = await _census_rows(session, _CENSUS_MIGRATION_QUERY)

    notes = ["table row counts:"]
    notes += [f"  {row['table_name']}: {_as_int(row['row_count'])}" for row in counts]

    if lifecycle:
        notes.append(
            f"track_mappings lifecycle: {_as_int(lifecycle[0]['live'])} live / "
            f"{_as_int(lifecycle[0]['superseded'])} superseded"
        )

    if by_source:
        notes.append("connector_plays by import_source:")
        notes += [
            f"  {row['import_source']}: {_as_int(row['row_count'])} "
            f"({row['first_played_at']} .. {row['last_played_at']})"
            for row in by_source
        ]
    else:
        notes.append("connector_plays by import_source: (none)")

    if play_range and _as_int(play_range[0]["row_count"]):
        notes.append(
            f"track_plays range: {play_range[0]['first_played_at']} .. "
            f"{play_range[0]['last_played_at']}"
        )

    notes.append("rows per user (tenanted tables):")
    notes += [
        f"  {row['table_name']} / {row['user_id']}: {_as_int(row['row_count'])}"
        for row in tenancy
    ]

    if migration:
        notes.append(f"alembic version: {migration[0]['version_num']}")

    return CheckResult(
        name="census",
        verdict="INFO",
        count=sum(_as_int(row["row_count"]) for row in counts),
        query=_CENSUS_COUNTS_QUERY,
        notes=notes,
    )


CHECKS: dict[str, Callable[[AsyncSession], Awaitable[CheckResult]]] = {
    "census": check_census,
    "discard_plausibility": check_discard_plausibility,
    "listen_threshold_discards": check_listen_threshold_discards,
    "short_track_blind_spot": check_short_track_blind_spot,
    "spotify_api_lastfm_delta": check_spotify_api_lastfm_delta,
}


def _render(result: CheckResult) -> None:
    print(f"\n=== {result.name}: {result.verdict} (n={result.count}) ===")
    for note in result.notes:
        print(f"  {note}")
    for exemplar in result.exemplars:
        print(f"  · {exemplar}")


async def main() -> None:
    setup_script_logger("audit_play_integrity")

    args = sys.argv[1:]
    if "--list" in args:
        print("Available checks:")
        for name in CHECKS:
            print(f"  {name}")
        return

    selected = list(CHECKS)
    if "--check" in args:
        name = args[args.index("--check") + 1]
        if name not in CHECKS:
            print(f"Unknown check: {name}. Use --list to see available checks.")
            return
        selected = [name]

    async with get_session() as session:
        for name in selected:
            _render(await CHECKS[name](session))


if __name__ == "__main__":
    asyncio.run(main())
