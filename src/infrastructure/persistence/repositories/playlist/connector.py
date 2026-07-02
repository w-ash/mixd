"""Connector playlist repository implementation."""

from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_logger
from src.domain.entities import ConnectorPlaylist
from src.infrastructure.persistence.database.db_models import DBConnectorPlaylist
from src.infrastructure.persistence.repositories.base_repo import BaseRepository
from src.infrastructure.persistence.repositories.playlist.mapper import (
    ConnectorPlaylistMapper,
)
from src.infrastructure.persistence.repositories.repo_decorator import db_operation

# Create module logger
logger = get_logger(__name__)


class ConnectorPlaylistRepository(
    BaseRepository[DBConnectorPlaylist, ConnectorPlaylist]
):
    """Repository for connector playlist operations."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize repository with session and mapper."""
        super().__init__(
            session=session,
            model_class=DBConnectorPlaylist,
            mapper=ConnectorPlaylistMapper(),
        )

    @db_operation("upsert_model")
    async def upsert_model(
        self, connector_playlist: ConnectorPlaylist
    ) -> ConnectorPlaylist:
        """Upsert a connector playlist directly from a domain model.

        This method preserves all properties of the domain model, including items.

        Args:
            connector_playlist: Complete domain model to persist

        Returns:
            Persisted connector playlist with ID
        """
        # Use lookup by connector name and ID
        lookup_attrs = {
            "connector_name": connector_playlist.connector_name,
            "connector_playlist_identifier": connector_playlist.connector_playlist_identifier,
        }

        # Convert domain model to dict for database
        db_model = self.mapper.to_db(connector_playlist)

        # Extract create attributes from the DB model
        create_attrs = {
            attr: getattr(db_model, attr)
            for attr in [
                "name",
                "description",
                "owner",
                "owner_id",
                "is_public",
                "collaborative",
                "follower_count",
                "items",
                "raw_metadata",
                "snapshot_id",
                "last_updated",
            ]
        }

        return await self.upsert(
            lookup_attrs=lookup_attrs,
            create_attrs=create_attrs,
        )

    @db_operation("list_by_connector")
    async def list_by_connector(self, connector: str) -> list[ConnectorPlaylist]:
        """List every cached playlist for a connector (cross-user cache)."""
        stmt = (
            self
            .select()
            .where(self.model_class.connector_name == connector)
            .order_by(self.model_class.name)
        )
        db_entities = await self._execute_query(stmt)
        return [await self.mapper.to_domain(db) for db in db_entities]

    @db_operation("bulk_upsert_models")
    async def bulk_upsert_models(
        self, connector_playlists: Sequence[ConnectorPlaylist]
    ) -> list[ConnectorPlaylist]:
        """Bulk upsert N connector playlists in a single round-trip.

        Batch-first counterpart to ``upsert_model``; the single-row method
        is the one-element degenerate case. Returns the persisted domain
        models with IDs populated via RETURNING.
        """
        if not connector_playlists:
            return []

        entities = [
            ConnectorPlaylistMapper.to_values_dict(cp) for cp in connector_playlists
        ]
        return await self.bulk_upsert(
            entities,
            lookup_keys=["connector_name", "connector_playlist_identifier"],
            return_models=True,
        )
