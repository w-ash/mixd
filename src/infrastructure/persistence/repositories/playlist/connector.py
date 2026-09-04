"""Connector playlist repository implementation."""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import ColumnElement, Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_logger
from src.domain.entities import ConnectorPlaylist
from src.domain.entities.playlist import ConnectorPlaylistSummary
from src.infrastructure.persistence.database.db_models import DBConnectorPlaylist
from src.infrastructure.persistence.repositories.base_repo import BaseRepository
from src.infrastructure.persistence.repositories.playlist.mapper import (
    ConnectorPlaylistMapper,
    ConnectorPlaylistSummaryRow,
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

    @db_operation("list_summaries_by_connector")
    async def list_summaries_by_connector(
        self, connector: str
    ) -> list[ConnectorPlaylistSummary]:
        """List every cached playlist for a connector (cross-user cache).

        An explicit column projection: every metadata column plus
        ``jsonb_array_length(items)``, so the items array is counted in
        PostgreSQL and never transferred. ``items`` defaults to ``[]`` and is
        NOT NULL, so the length is always defined.
        """
        item_count: ColumnElement[int] = func.jsonb_array_length(
            DBConnectorPlaylist.items
        )
        # The projection exceeds select()'s typed overloads, which stop at ten
        # columns; the annotation restores the row shape ``to_summary`` unpacks.
        stmt: Select[ConnectorPlaylistSummaryRow] = (
            select(
                DBConnectorPlaylist.id,
                DBConnectorPlaylist.connector_name,
                DBConnectorPlaylist.connector_playlist_identifier,
                DBConnectorPlaylist.name,
                DBConnectorPlaylist.description,
                DBConnectorPlaylist.owner,
                DBConnectorPlaylist.owner_id,
                DBConnectorPlaylist.is_public,
                DBConnectorPlaylist.collaborative,
                DBConnectorPlaylist.follower_count,
                DBConnectorPlaylist.raw_metadata,
                DBConnectorPlaylist.snapshot_id,
                DBConnectorPlaylist.last_updated,
                item_count.label("item_count"),
            )
            .where(DBConnectorPlaylist.connector_name == connector)
            .order_by(DBConnectorPlaylist.name)
        )
        rows = (await self.session.execute(stmt)).tuples().all()
        return [ConnectorPlaylistMapper.to_summary(row) for row in rows]

    @db_operation("find_by_identifiers")
    async def find_by_identifiers(
        self, connector: str, identifiers: Sequence[str]
    ) -> list[ConnectorPlaylist]:
        """Fetch the named connector playlists in one round-trip."""
        if not identifiers:
            return []
        stmt = self.select().where(
            self.model_class.connector_name == connector,
            self.model_class.connector_playlist_identifier.in_(identifiers),
        )
        db_entities = await self._execute_query(stmt)
        return [await self.mapper.to_domain(db) for db in db_entities]

    @db_operation("find_by_ids")
    async def find_by_ids(self, ids: Sequence[UUID]) -> list[ConnectorPlaylist]:
        """Fetch connector playlists by internal ID in one round-trip."""
        if not ids:
            return []
        stmt = self.select().where(self.model_class.id.in_(ids))
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
