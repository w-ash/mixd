"""Repository for playlist link (mapping) operations.

Maps DBPlaylistMapping to PlaylistLink domain entities with sync management.
Joins through DBConnectorPlaylist to denormalize the external playlist identifier and name.
"""

# pyright: reportExplicitAny=false, reportAttributeAccessIssue=false, reportUnknownMemberType=false, reportAny=false
# Legitimate Any: SQLAlchemy column expressions

from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from src.config import get_logger
from src.domain.entities.playlist_link import PlaylistLink, SyncDirection, SyncStatus
from src.infrastructure.persistence.database.db_models import (
    DBConnectorPlaylist,
    DBPlaylistMapping,
)
from src.infrastructure.persistence.repositories.repo_decorator import db_operation

logger = get_logger(__name__)


def _to_link(db: DBPlaylistMapping, cp: DBConnectorPlaylist) -> PlaylistLink:
    """Convert DB mapping + connector playlist to domain PlaylistLink."""
    return PlaylistLink(
        id=db.id,
        playlist_id=db.playlist_id,
        connector_name=db.connector_name,
        connector_playlist_identifier=cp.connector_playlist_identifier,
        connector_playlist_name=cp.name,
        sync_direction=SyncDirection(db.sync_direction),
        sync_status=SyncStatus(db.sync_status),
        last_synced=db.last_sync_completed_at,
        last_sync_error=db.last_sync_error,
        last_sync_tracks_added=db.last_sync_tracks_added,
        last_sync_tracks_removed=db.last_sync_tracks_removed,
        created_at=db.created_at,
    )


class PlaylistLinkRepository:
    """Repository for managing playlist links (canonical ↔ external mappings).

    Operates on DBPlaylistMapping but exposes typed PlaylistLink domain entities.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @db_operation("get_links_for_playlist")
    async def get_links_for_playlist(self, playlist_id: int) -> list[PlaylistLink]:
        """Get all connector links for a canonical playlist."""
        stmt = (
            select(DBPlaylistMapping)
            .where(DBPlaylistMapping.playlist_id == playlist_id)
            .options(joinedload(DBPlaylistMapping.connector_playlist))
        )
        result = await self._session.execute(stmt)
        rows = result.scalars().all()

        return [
            _to_link(db_mapping, db_mapping.connector_playlist) for db_mapping in rows
        ]

    @db_operation("get_link")
    async def get_link(self, link_id: int) -> PlaylistLink | None:
        """Get a single playlist link by ID."""
        stmt = (
            select(DBPlaylistMapping)
            .where(DBPlaylistMapping.id == link_id)
            .options(joinedload(DBPlaylistMapping.connector_playlist))
        )
        result = await self._session.execute(stmt)
        db_mapping = result.scalar_one_or_none()

        if db_mapping is None:
            return None

        return _to_link(db_mapping, db_mapping.connector_playlist)

    @db_operation("create_link")
    async def create_link(self, link: PlaylistLink) -> PlaylistLink:
        """Create a new playlist link.

        Expects the DBConnectorPlaylist to already exist (created during validation).
        Looks it up by connector_name + connector_playlist_identifier.
        """
        # Find the connector playlist DB record
        cp_stmt = select(DBConnectorPlaylist).where(
            DBConnectorPlaylist.connector_name == link.connector_name,
            DBConnectorPlaylist.connector_playlist_identifier
            == link.connector_playlist_identifier,
        )
        result = await self._session.execute(cp_stmt)
        cp = result.scalar_one_or_none()

        if cp is None:
            raise ValueError(
                f"ConnectorPlaylist not found: {link.connector_name}:{link.connector_playlist_identifier}"
            )

        db_mapping = DBPlaylistMapping(
            playlist_id=link.playlist_id,
            connector_name=link.connector_name,
            connector_playlist_id=cp.id,
            sync_direction=link.sync_direction.value,
            sync_status=link.sync_status.value,
        )
        self._session.add(db_mapping)
        await self._session.flush()

        return _to_link(db_mapping, cp)

    @db_operation("update_sync_status")
    async def update_sync_status(
        self,
        link_id: int,
        status: SyncStatus,
        *,
        error: str | None = None,
        tracks_added: int | None = None,
        tracks_removed: int | None = None,
    ) -> None:
        """Update sync status and optional metrics for a link."""
        values: dict[str, object] = {
            "sync_status": status.value,
        }

        if status == SyncStatus.SYNCING:
            values["last_sync_started_at"] = datetime.now(UTC)
            values["last_sync_error"] = None
        elif status == SyncStatus.SYNCED:
            values["last_sync_completed_at"] = datetime.now(UTC)
            values["last_sync_error"] = None
            if tracks_added is not None:
                values["last_sync_tracks_added"] = tracks_added
            if tracks_removed is not None:
                values["last_sync_tracks_removed"] = tracks_removed
        elif status == SyncStatus.ERROR:
            values["last_sync_error"] = error

        stmt = (
            update(DBPlaylistMapping)
            .where(DBPlaylistMapping.id == link_id)
            .values(**values)
        )
        await self._session.execute(stmt)

    @db_operation("count_links_by_connector")
    async def count_links_by_connector(self) -> dict[str, int]:
        """Count linked playlists grouped by connector name."""
        stmt = select(
            DBPlaylistMapping.connector_name,
            func.count(DBPlaylistMapping.id),
        ).group_by(DBPlaylistMapping.connector_name)
        result = await self._session.execute(stmt)
        return {name: count for name, count in result.tuples().all()}

    @db_operation("update_link_direction")
    async def update_link_direction(
        self, link_id: int, direction: SyncDirection
    ) -> PlaylistLink | None:
        """Update the sync direction for a link. Returns the updated link."""
        stmt = (
            update(DBPlaylistMapping)
            .where(DBPlaylistMapping.id == link_id)
            .values(sync_direction=direction.value)
        )
        result = await self._session.execute(stmt)
        if result.rowcount == 0:
            return None
        return await self.get_link(link_id)

    @db_operation("delete_link")
    async def delete_link(self, link_id: int) -> bool:
        """Delete a playlist link. Returns True if deleted."""
        stmt = select(DBPlaylistMapping).where(DBPlaylistMapping.id == link_id)
        result = await self._session.execute(stmt)
        db_mapping = result.scalar_one_or_none()

        if db_mapping is None:
            return False

        await self._session.delete(db_mapping)
        return True
