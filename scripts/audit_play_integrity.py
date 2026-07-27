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

    The GDPR export's END-time semantics were only ever confirmed empirically
    (findings §3); the API's are assumed START until this says otherwise. Until
    it does, the channel keeps its wide ``tolerance_override``.

    Read the median: near 0 confirms START. Near +duration_ms means the API
    stamps the END, and the correction ships as ledger-carried data.
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
                "No spotify_api rows yet — run a recently-played import first "
                "(`mixd history import-spotify-recent`)."
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
                    "START confirmed — safe to drop the tolerance_override"
                    if abs(median) <= _TIGHT_AGREEMENT_SECONDS
                    else f"NOT start-aligned; compare |{median:+.1f}s| against "
                    f"the {median_duration:.1f}s duration for an END signature"
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


CHECKS: dict[str, Callable[[AsyncSession], Awaitable[CheckResult]]] = {
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
