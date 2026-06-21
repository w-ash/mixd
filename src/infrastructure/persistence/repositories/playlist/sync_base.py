"""Repository for per-link playlist sync bases (DBPlaylistSyncBase).

Stores/loads the connector snapshot id a link last reconciled to — small by
construction (identity + snapshot, no track payloads). No delete method: the
FK's ON DELETE CASCADE removes the base when its link goes.
"""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_logger
from src.domain.entities.playlist_sync_base import PlaylistSyncBase
from src.infrastructure.persistence.database.db_models import DBPlaylistSyncBase
from src.infrastructure.persistence.repositories.repo_decorator import db_operation

logger = get_logger(__name__)


def _to_domain(db: DBPlaylistSyncBase) -> PlaylistSyncBase:
    return PlaylistSyncBase(
        id=db.id,
        link_id=db.link_id,
        user_id=db.user_id,
        connector_name=db.connector_name,
        connector_playlist_identifier=db.connector_playlist_identifier,
        base_snapshot_id=db.base_snapshot_id,
        base_taken_at=db.base_taken_at,
    )


class PlaylistSyncBaseRepository:
    """Persistence for a link's last-reconciled external base snapshot."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @db_operation("get_sync_base_for_link")
    async def get_for_link(self, link_id: UUID) -> PlaylistSyncBase | None:
        """Return the base for a link, or None if it has never synced."""
        stmt = select(DBPlaylistSyncBase).where(DBPlaylistSyncBase.link_id == link_id)
        db = (await self._session.execute(stmt)).scalar_one_or_none()
        return _to_domain(db) if db is not None else None

    @db_operation("upsert_sync_base")
    async def upsert(self, base: PlaylistSyncBase) -> PlaylistSyncBase:
        """Insert or replace the base for a link (one base per link)."""
        now = datetime.now(UTC)
        stmt = (
            pg_insert(DBPlaylistSyncBase)
            .values(
                id=base.id,
                user_id=base.user_id,
                link_id=base.link_id,
                connector_name=base.connector_name,
                connector_playlist_identifier=base.connector_playlist_identifier,
                base_snapshot_id=base.base_snapshot_id,
                base_taken_at=base.base_taken_at,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_update(
                constraint="uq_playlist_sync_bases_link",
                set_={
                    "base_snapshot_id": base.base_snapshot_id,
                    "base_taken_at": base.base_taken_at,
                    "connector_name": base.connector_name,
                    "connector_playlist_identifier": base.connector_playlist_identifier,
                    "updated_at": now,
                },
            )
        )
        await self._session.execute(stmt)
        await self._session.flush()
        stored = await self.get_for_link(base.link_id)
        if stored is None:  # pragma: no cover - just upserted
            raise RuntimeError("Sync base missing immediately after upsert")
        return stored
