#!/usr/bin/env python3
"""One-shot backfill: stamp ``start_shift_ms`` onto pre-calibration spotify_api rows.

Spotify's ``/me/player/recently-played`` stamps the **END** of a play, not the
start (v0.10.3 C1). The importer now derives each play's END→START shift and
writes it to the ledger as ``service_metadata.start_shift_ms``, which is what
:func:`src.domain.matching.play_projection.start_shift` reads to normalize the
observation to the start of the listen. Rows imported *before* that landed carry
no such key: they normalize to their raw end stamp, sit a track-length adrift of
every other channel's view of the same listen, and therefore pair only at the
wide 180s fallback tolerance — i.e. they never merge with the export's or
Last.fm's observation of the same event. They do carry ``duration_ms``, so the
shift is recoverable after the fact. This script recovers it.

**The rule is not reimplemented here.** The stamp/refuse decision is
``SpotifyRecentlyPlayedImporter._derived_start_shift_ms`` itself, called with
ledger rows rehydrated into the ``SpotifyPlayHistoryItem`` shape it reads (it
touches exactly ``played_at`` and ``track.duration_ms``). A second copy of the
rule would be free to drift from the importer's, and a backfill that disagrees
with the importer is the exact defect class this milestone exists to close.

In short, the rule: because ``played_at`` is an end stamp, the wall clock
between consecutive stamps IS the later play's listened time, so the track's
duration is only that play's shift when the two agree within 15s. A shortfall
means the play was cut off; an overrun means something sat idle before it.
Neither is stamped — a wrong correction moves the play further from the truth
than no correction.

**Predecessors and poll boundaries.** The importer walks the entries of one poll
response; the ledger holds rows from many polls, so here the predecessor is the
previous row by ``(played_at, id)`` within one user's ``spotify_api`` channel,
across batch boundaries. Two consequences, both deliberate:

- Where listening ran continuously across two polls, the ledger's neighbours are
  the true neighbours, so a row the importer refused as "first entry of a poll"
  can legitimately be stamped here. That is a strict improvement, not a
  divergence: the rule being applied is the same one, on better-informed input.
- Where the true predecessor was never stored (the previous poll's window did
  not reach it, or the entry was dropped as unusable), the elapsed gap spans the
  missing play and overruns the duration, so the row is refused. The refusal is
  what makes crossing poll boundaries safe — the rule cannot be fooled by a gap
  without also being told a track that long played inside it.

Rows that already carry ``start_shift_ms`` are never rewritten (the importer's
own value is authoritative) but still act as predecessors — which is also what
makes the script idempotent: a second run finds every row it stamped already
stamped, and every refusal is deterministic in data this script does not touch.

⚠️ **``mixd plays rebuild`` is the required follow-up.** ``start_shift_ms``
changes an observation's *normalized start*, and canonical ``track_plays`` are
a projection of the ledger — they do not move until the projection is re-run.
Between ``--apply`` and the rebuild, the ledger and canonical history disagree.

**One user per run.** ``connector_plays`` is RLS-FORCED on
``current_setting('app.user_id')``; ``--user`` sets the context the whole run
executes in, and the scan filters on it explicitly as well so a role that
bypasses RLS still reads exactly one tenant.

Safety:
- **Dry-run by default** — prints the verdict for every row. Pass ``--apply``.
- **Idempotent** — the UPDATE re-checks "has no start_shift_ms" in its own
  WHERE clause, so a concurrent import's value can never be overwritten.

Usage:
    uv run python scripts/backfill_spotify_api_start_shift.py --user <id>
    uv run python scripts/backfill_spotify_api_start_shift.py --user <id> --apply
"""

import asyncio
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime
from itertools import starmap
import statistics
from typing import Final
from uuid import UUID

from attrs import define
from sqlalchemy import select, text
import typer

from src.config import get_logger, setup_script_logger
from src.config.constants import BusinessLimits
from src.domain.matching.play_projection import START_SHIFT_MS_KEY
from src.infrastructure.connectors.spotify.models import (
    SpotifyPlayHistoryItem,
    SpotifyTrack,
)
from src.infrastructure.connectors.spotify.recently_played_importer import (
    SpotifyRecentlyPlayedImporter,
)
from src.infrastructure.persistence.database.db_connection import get_session
from src.infrastructure.persistence.database.db_models import DBConnectorPlay
from src.infrastructure.persistence.database.user_context import user_context

logger = get_logger(__name__)

_IMPORT_SOURCE: Final = "spotify_api"
_CONNECTOR: Final = "spotify"
_MS_PER_SECOND: Final = 1000

# Verdict vocabulary. Only STAMPED writes; the rest are refusals, and the four
# refusal names describe *why* the shared rule declined — they never decide.
STAMPED: Final = "stamped"
ALREADY_STAMPED: Final = "already_stamped"
NO_PREDECESSOR: Final = "no_predecessor"
NO_DURATION: Final = "no_duration"
SHORTFALL: Final = "shortfall"
OVERRUN: Final = "overrun"

_BACKFILL_SQL: Final = text("""
    UPDATE connector_plays
    SET raw_metadata = jsonb_set(
        raw_metadata,
        '{service_metadata,start_shift_ms}',
        to_jsonb(CAST(:shift_ms AS integer)),
        true
    )
    WHERE id = CAST(:row_id AS uuid)
      AND NOT jsonb_exists(raw_metadata -> 'service_metadata', :shift_key)
""")


@define(frozen=True, slots=True)
class Observation:
    """One ``spotify_api`` ledger row in the shape the importer's rule reads.

    ``item`` carries only what ``_derived_start_shift_ms`` touches —
    ``played_at`` and ``track.duration_ms``; the id/name are filled from the row
    for legibility in debugging, not because the rule consults them.
    """

    row_id: UUID
    item: SpotifyPlayHistoryItem
    already_stamped: bool


@define(frozen=True, slots=True)
class Verdict:
    """What the shared rule decided for one row, and why."""

    row_id: UUID
    played_at: datetime
    shift_ms: int | None
    reason: str


def _int_or_zero(value: object) -> int:
    """A positive int, or 0 — the "no duration" input the shared rule refuses."""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return 0
    return value


def to_observation(
    row_id: UUID,
    played_at: datetime,
    raw_metadata: Mapping[str, object] | None,
    connector_track_identifier: str,
) -> Observation:
    """Rehydrate a ledger row into the payload shape the importer's rule reads."""
    raw = raw_metadata or {}
    nested = raw.get("service_metadata")
    metadata: Mapping[str, object] = nested if isinstance(nested, dict) else {}
    track_name = raw.get("track_name")
    uri = metadata.get("track_uri")
    identifier = uri if isinstance(uri, str) else connector_track_identifier
    return Observation(
        row_id=row_id,
        item=SpotifyPlayHistoryItem(
            track=SpotifyTrack(
                id=identifier.removeprefix("spotify:track:"),
                name=track_name if isinstance(track_name, str) else "",
                duration_ms=_int_or_zero(metadata.get("duration_ms")),
            ),
            played_at=played_at,
        ),
        already_stamped=START_SHIFT_MS_KEY in metadata,
    )


def _refusal_reason(
    item: SpotifyPlayHistoryItem, previous: SpotifyPlayHistoryItem | None
) -> str:
    """Label an *already-made* refusal. Reporting only — it decides nothing.

    Mirrors the short-circuit order of the shared rule so the label names the
    condition that actually stopped it, and splits the remaining case by the
    sign of the mismatch alone — no tolerance threshold is re-tested here.
    """
    if previous is None:
        return NO_PREDECESSOR
    if item.track.duration_ms <= 0:
        return NO_DURATION
    elapsed_ms = (item.played_at - previous.played_at).total_seconds() * _MS_PER_SECOND
    return SHORTFALL if elapsed_ms < item.track.duration_ms else OVERRUN


def plan(observations: Sequence[Observation]) -> list[Verdict]:
    """Decide a shift for each observation, in ``played_at`` order.

    The decision is the importer's own ``_derived_start_shift_ms`` — deliberately
    reached into rather than restated, so the backfill and the import path can
    never disagree about what a full play is.
    """
    verdicts: list[Verdict] = []
    previous: SpotifyPlayHistoryItem | None = None
    for observation in observations:
        item = observation.item
        shift_ms = SpotifyRecentlyPlayedImporter._derived_start_shift_ms(item, previous)
        if observation.already_stamped:
            reason, shift_ms = ALREADY_STAMPED, None
        elif shift_ms is not None:
            reason = STAMPED
        else:
            reason = _refusal_reason(item, previous)
        verdicts.append(
            Verdict(
                row_id=observation.row_id,
                played_at=item.played_at,
                shift_ms=shift_ms,
                reason=reason,
            )
        )
        previous = item
    return verdicts


async def _load_observations(user_id: str) -> list[Observation]:
    """Every ``spotify_api`` ledger row for one user, in predecessor order.

    Column-level rather than whole-entity: the four columns the rule needs are
    stable, whereas selecting the mapped entity would fail on any environment
    whose schema trails the ORM by a column it never reads.
    """
    stmt = (
        select(
            DBConnectorPlay.id,
            DBConnectorPlay.played_at,
            DBConnectorPlay.raw_metadata,
            DBConnectorPlay.connector_track_identifier,
        )
        .where(
            DBConnectorPlay.user_id == user_id,
            DBConnectorPlay.connector_name == _CONNECTOR,
            DBConnectorPlay.import_source == _IMPORT_SOURCE,
        )
        .order_by(DBConnectorPlay.played_at, DBConnectorPlay.id)
    )
    async with get_session() as session:
        result = await session.execute(stmt)
        return list(starmap(to_observation, result.all()))


async def _write(verdicts: Sequence[Verdict]) -> int:
    """Stamp the qualifying rows. Returns the number of rows actually updated."""
    written = 0
    async with get_session() as session:
        for verdict in verdicts:
            if verdict.shift_ms is None:
                continue
            result = await session.execute(
                _BACKFILL_SQL,
                {
                    "row_id": str(verdict.row_id),
                    "shift_ms": verdict.shift_ms,
                    "shift_key": START_SHIFT_MS_KEY,
                },
            )
            written += result.rowcount
    return written


def _report(verdicts: Sequence[Verdict]) -> None:
    """Log the verdict census and the shift distribution."""
    census = Counter(verdict.reason for verdict in verdicts)
    logger.info(
        "Verdicts",
        total=len(verdicts),
        stamped=census[STAMPED],
        already_stamped=census[ALREADY_STAMPED],
        refused_no_predecessor=census[NO_PREDECESSOR],
        refused_no_duration=census[NO_DURATION],
        refused_shortfall=census[SHORTFALL],
        refused_overrun=census[OVERRUN],
    )

    shifts = sorted(v.shift_ms for v in verdicts if v.shift_ms is not None)
    if not shifts:
        return
    buckets = Counter(f"{shift // 60_000}-{shift // 60_000 + 1}min" for shift in shifts)
    logger.info(
        "Shift distribution (seconds)",
        count=len(shifts),
        min=round(shifts[0] / _MS_PER_SECOND, 1),
        median=round(statistics.median(shifts) / _MS_PER_SECOND, 1),
        max=round(shifts[-1] / _MS_PER_SECOND, 1),
        buckets=dict(sorted(buckets.items())),
    )


async def _run(*, apply: bool, user: str) -> None:
    setup_script_logger("backfill_spotify_api_start_shift")
    observations = await _load_observations(user)
    logger.info(
        "spotify_api ledger rows",
        total=len(observations),
        user_id=user,
        mode="APPLY" if apply else "DRY-RUN",
    )
    if not observations:
        logger.info("Nothing to backfill — no spotify_api rows for this user.")
        return

    verdicts = plan(observations)
    _report(verdicts)

    if not apply:
        logger.info("Dry-run only. Re-run with --apply to stamp the qualifying rows.")
        return

    written = await _write(verdicts)
    logger.info("Backfill complete", rows_stamped=written)
    logger.warning(
        "REQUIRED FOLLOW-UP: run `mixd plays rebuild`. start_shift_ms moves an "
        "observation's normalized start, and canonical track_plays are a "
        "projection of the ledger — until the rebuild runs, the ledger and "
        "canonical history disagree about when these plays started."
    )


def main(
    apply: bool = typer.Option(
        default=False,
        help="Write start_shift_ms. Without it the script is a dry-run.",
    ),
    user: str = typer.Option(
        default=BusinessLimits.DEFAULT_USER_ID,
        help="The mixd user_id to run as — sets the RLS context for the whole run.",
    ),
) -> None:
    """Backfill start_shift_ms on pre-calibration spotify_api rows (dry-run default)."""
    with user_context(user):
        asyncio.run(_run(apply=apply, user=user))


if __name__ == "__main__":
    typer.run(main)
