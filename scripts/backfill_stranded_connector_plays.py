#!/usr/bin/env python3
"""One-shot backfill: re-resolve connector_plays stranded by the v0.8.5 SSE-seam bug.

The web two-phase import bug (v0.8.5 Story 2) committed phase-1 ``connector_plays``
but aborted phase-2 resolution under the request-bound emitter, leaving connector
plays with **no corresponding ``track_play``**. This script finds those stranded rows
and re-runs resolution for them only.

Detection is an ANTI-JOIN — connector_plays with no track_play at the same
``(user_id, service, played_at)`` — NOT ``resolved_track_id IS NULL``. That column is
only written at insert time and never back-filled after resolution, so it is NULL on
*every* row and cannot distinguish stranded from resolved (the spec's premise was
wrong; verified against the writes).

Safety:
- **Dry-run by default** — prints what it *would* re-resolve. Pass ``--apply`` to write.
- **Idempotent** — track_play inserts are ON CONFLICT DO NOTHING, and a play that was
  legitimately suppressed by cross-source dedup simply re-suppresses (a harmless no-op),
  so re-running can never duplicate or corrupt.

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

from sqlalchemy import select
import typer

from src.config import get_logger, setup_script_logger
from src.domain.entities import ConnectorTrackPlay
from src.domain.entities.progress import NullProgressEmitter
from src.infrastructure.persistence.database.db_connection import get_session
from src.infrastructure.persistence.database.db_models import (
    DBConnectorPlay,
    DBTrackPlay,
)

logger = get_logger(__name__)


async def _find_stranded(
    since: datetime | None, user: str | None
) -> list[DBConnectorPlay]:
    """Return connector_plays with no track_play at the same user+service+played_at."""
    # LEFT JOIN anti-join: connector plays that never produced a canonical track play.
    stmt = (
        select(DBConnectorPlay)
        .outerjoin(
            DBTrackPlay,
            (DBTrackPlay.user_id == DBConnectorPlay.user_id)
            & (DBTrackPlay.service == DBConnectorPlay.connector_name)
            & (DBTrackPlay.played_at == DBConnectorPlay.played_at),
        )
        .where(DBTrackPlay.id.is_(None))
    )
    if since is not None:
        stmt = stmt.where(DBConnectorPlay.played_at >= since)
    if user is not None:
        stmt = stmt.where(DBConnectorPlay.user_id == user)

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


async def _run(*, apply: bool, since: datetime | None, user: str | None) -> None:
    setup_script_logger("backfill_stranded_connector_plays")
    rows = await _find_stranded(since, user)

    by_user: dict[str, list[ConnectorTrackPlay]] = defaultdict(list)
    for row in rows:
        by_user[row.user_id].append(_to_domain(row))

    total = sum(len(v) for v in by_user.values())
    logger.info(
        "Stranded connector_plays found",
        total=total,
        users=len(by_user),
        mode="APPLY" if apply else "DRY-RUN",
    )
    for user_id, plays in sorted(by_user.items()):
        by_service: dict[str, int] = defaultdict(int)
        for p in plays:
            by_service[p.service] += 1
        logger.info("  user", user_id=user_id, by_service=dict(by_service))

    if total == 0:
        logger.info("Nothing to backfill — no stranded plays matched.")
        return
    if not apply:
        logger.info("Dry-run only. Re-run with --apply to re-resolve these plays.")
        return

    resolved_total = 0
    for user_id, plays in sorted(by_user.items()):
        resolved = await _resolve_group(user_id, plays)
        resolved_total += resolved
        logger.info("Re-resolved", user_id=user_id, resolved=resolved)
    logger.info("Backfill complete", resolved_total=resolved_total, considered=total)


def main(
    apply: bool = typer.Option(
        default=False,
        help="Write the re-resolved track_plays. Without it the script is a dry-run.",
    ),
    since: str | None = typer.Option(
        default=None,
        help="Only consider connector_plays played on/after this date (YYYY-MM-DD).",
    ),
    user: str | None = typer.Option(
        default=None, help="Restrict the backfill to a single mixd user_id."
    ),
) -> None:
    """Re-resolve stranded connector_plays (dry-run unless --apply)."""
    since_dt = (
        datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=UTC)
        if since is not None
        else None
    )
    asyncio.run(_run(apply=apply, since=since_dt, user=user))


if __name__ == "__main__":
    typer.run(main)
