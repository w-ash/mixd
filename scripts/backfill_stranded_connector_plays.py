#!/usr/bin/env python3
"""One-shot backfill: re-resolve and re-project stranded connector_plays.

A killed import leaves ledger rows that never became canonical plays. Two shapes
produce that state, and this script recovers both:

- **Never resolved** — ``resolved_track_id IS NULL``. Phase 1 committed the rows,
  Phase 2 died before stamping them. This is the v0.8.5 SSE-seam bug and, at much
  larger scale, the killed 54-minute import whose 15,317 in-memory resolutions were
  discarded wholesale (fixed since by per-chunk write-back, which bounds the loss to
  one chunk).
- **Resolved but unprojected** — stamped in a committed chunk, then killed before the
  end-of-run projection swept the touched days. The residual gap per-chunk write-back
  deliberately accepts, because projection is day-scoped and cheap to re-run.

Detection is therefore an ANTI-JOIN against ``play_sources``: an observation with no
membership edge is not part of canonical history, whichever way it got there. The
edge is exact — the projection writes one per member observation — whereas the
former ``(user_id, service, played_at)`` join against ``track_plays`` compares a
ledger timestamp against a canonical one the projection *derives* from the whole
observation group, so it reported healthy rows as stranded.

**The anti-join alone is not sufficient, and was never the whole test.** A
deliberately-excluded observation also has no membership edge: a skip under the
listen threshold, or a private-session play, is *supposed* to have no canonical
play. Before v0.10.3 those were indistinguishable from failures — 80,209 rows in
the reference corpus, which is the finding that added ``exclusion_reason``. Now
they are distinguishable, so the query says so: rows carrying a *deliberate*
exclusion reason are never candidates. Without that predicate this script
offered to re-resolve 80,243 rows where 51 were genuinely stranded, and
``--apply`` would have projected ~80k skipped plays into canonical history.

Re-upload of the source file is the other recovery route and needs no script: Phase 1
forwards every parsed play, not only newly-inserted ones, and the resolution
write-back matches on the ledger natural key. Prefer this script when the source file
is gone or when only the projection half is missing.

**One user per run.** ``connector_plays`` is RLS-FORCED on
``current_setting('app.user_id')``, so a cross-tenant scan cannot see anything and a
write under the wrong context is silently rejected. ``--user`` sets the context the
whole run executes in.

Safety:
- **Dry-run by default** — prints what it *would* re-resolve. Pass ``--apply`` to write.
- **Idempotent** — resolution write-back and play_source upserts are ON CONFLICT, and
  the projection's diff-apply is a no-op against unchanged days, so re-running can
  never duplicate or corrupt.

Re-resolution invokes the real per-service resolver (Last.fm cross-discovery hits the
Spotify API), so ``--apply`` needs the same credentials a normal import does.

Usage:
    uv run python scripts/backfill_stranded_connector_plays.py            # dry-run
    uv run python scripts/backfill_stranded_connector_plays.py --apply
    uv run python scripts/backfill_stranded_connector_plays.py --since 2026-05-01 --apply
    uv run python scripts/backfill_stranded_connector_plays.py --user <mixd_user_id>
"""

import asyncio
from collections import defaultdict
from datetime import UTC, datetime
from typing import Final

from sqlalchemy import or_, select
import typer

from src.config import get_logger, setup_script_logger
from src.config.constants import BusinessLimits
from src.domain.entities import ConnectorTrackPlay
from src.domain.entities.operations import PLAY_EXCLUSION_REASONS
from src.domain.entities.progress import NullProgressEmitter
from src.infrastructure.persistence.database.db_connection import get_session
from src.infrastructure.persistence.database.db_models import (
    DBConnectorPlay,
    DBPlaySource,
)
from src.infrastructure.persistence.database.user_context import (
    statement_timeout_context,
    user_context,
)

logger = get_logger(__name__)

# The one ``PlayExclusionReason`` that means "we could not identify the track",
# as opposed to "we identified it and left it out on purpose". Asserted against
# the domain vocabulary so a rename there fails loudly here instead of silently
# widening this script's candidate set back to every skipped play.
UNRESOLVED_EXCLUSION: Final = "unresolved"
if UNRESOLVED_EXCLUSION not in PLAY_EXCLUSION_REASONS:
    raise RuntimeError(
        f"{UNRESOLVED_EXCLUSION!r} is no longer a PlayExclusionReason — this "
        f"script's candidate filter is out of date and would re-resolve rows "
        f"that were excluded on purpose"
    )


async def _find_stranded(since: datetime | None, user: str) -> list[DBConnectorPlay]:
    """Return ``user``'s connector_plays with no ``play_sources`` membership edge.

    The tenant predicate is explicit and load-bearing. This function used to
    rely on ``user_context`` + RLS alone — but per PDR-002 the Neon role owns
    the tables and carries ``BYPASSRLS``, so in production the policies do not
    run and an unscoped scan sees every tenant. On 2026-08-20 that swept 34
    rows belonging to ``default`` into a run for another user and re-resolved
    them onto *that* user's tracks, re-creating the cross-tenant ledger state
    the mistenancy repair had just removed. RLS is defence in depth here, never
    the isolation itself.
    """
    stmt = (
        select(DBConnectorPlay)
        .outerjoin(
            DBPlaySource,
            DBPlaySource.connector_play_id == DBConnectorPlay.id,
        )
        .where(
            DBConnectorPlay.user_id == user,
            DBPlaySource.id.is_(None),
            # A deliberate exclusion is not a stranding: those rows have no
            # canonical play by design. ``unresolved`` stays a candidate — the
            # resolver tried and failed, and a later run (relink handling, a
            # restored id) can still succeed. NULL stays a candidate too: no
            # verdict was ever recorded, which is the killed-import shape.
            or_(
                DBConnectorPlay.exclusion_reason.is_(None),
                DBConnectorPlay.exclusion_reason == UNRESOLVED_EXCLUSION,
            ),
        )
    )
    if since is not None:
        stmt = stmt.where(DBConnectorPlay.played_at >= since)

    async with get_session() as session:
        result = await session.execute(stmt)
        return list(result.scalars().all())


def _to_domain(row: DBConnectorPlay) -> ConnectorTrackPlay:
    """Rebuild a ConnectorTrackPlay from the row's preserved raw_metadata."""
    meta = row.raw_metadata or {}
    album = meta.get("album_name")
    service_metadata = meta.get("service_metadata")
    return ConnectorTrackPlay(
        artist_name=str(meta.get("artist_name", "")),
        track_name=str(meta.get("track_name", "")),
        played_at=row.played_at,
        service=row.connector_name,
        user_id=row.user_id,
        album_name=album if isinstance(album, str) else None,
        ms_played=row.ms_played,
        service_metadata=service_metadata if isinstance(service_metadata, dict) else {},
        # Carry the channel through: build_play_context keys on
        # (service, import_source), so dropping it downgrades every rebuilt row
        # to the generic context and silently loses the channel-specific keys.
        import_source=row.import_source,
    )


async def _resolve_group(user_id: str, plays: list[ConnectorTrackPlay]) -> int:
    """Re-run resolution for one user's stranded plays. Returns resolved count."""
    from src.application.services.play_import_orchestrator import (
        PlayImportOrchestrator,
    )
    from src.infrastructure.persistence.repositories.factories import (
        get_unit_of_work,
    )
    from src.infrastructure.services.play_import_registry import (
        get_play_import_registry,
    )

    registry = get_play_import_registry()
    orchestrator = PlayImportOrchestrator(
        resolver_factory=registry.create_play_resolver
    )

    async with get_session() as session:
        uow = get_unit_of_work(session)
        result = await orchestrator.execute_resolution_phase(
            plays, uow, user_id=user_id, progress_emitter=NullProgressEmitter()
        )
        return int(result.summary_metrics.get("resolved"))


async def _run(*, apply: bool, since: datetime | None, user: str) -> None:
    setup_script_logger("backfill_stranded_connector_plays")
    rows = await _find_stranded(since, user)
    plays = [_to_domain(row) for row in rows]

    by_service: dict[str, int] = defaultdict(int)
    for play in plays:
        by_service[play.service] += 1
    logger.info(
        "Stranded connector_plays found",
        total=len(plays),
        user_id=user,
        by_service=dict(by_service),
        mode="APPLY" if apply else "DRY-RUN",
    )

    if not plays:
        logger.info("Nothing to backfill — no stranded plays matched.")
        return
    if not apply:
        logger.info("Dry-run only. Re-run with --apply to re-resolve these plays.")
        return

    resolved = await _resolve_group(user, plays)
    logger.info("Backfill complete", resolved=resolved, considered=len(plays))


def main(
    apply: bool = typer.Option(
        default=False,
        help="Write the re-resolved track_plays. Without it the script is a dry-run.",
    ),
    since: str | None = typer.Option(
        default=None,
        help="Only consider connector_plays played on/after this date (YYYY-MM-DD).",
    ),
    user: str = typer.Option(
        default=BusinessLimits.DEFAULT_USER_ID,
        help="The mixd user_id to run as — sets the RLS context for the whole run.",
    ),
) -> None:
    """Re-resolve stranded connector_plays (dry-run unless --apply)."""
    since_dt = (
        datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=UTC)
        if since is not None
        else None
    )
    # The scan and the write-back both have to see the same tenant, and the
    # projection's day sweep can outrun the 30s connection default the way a
    # bulk import can.
    with (
        user_context(user),
        statement_timeout_context(BusinessLimits.BULK_IMPORT_STATEMENT_TIMEOUT),
    ):
        asyncio.run(_run(apply=apply, since=since_dt, user=user))


if __name__ == "__main__":
    typer.run(main)
