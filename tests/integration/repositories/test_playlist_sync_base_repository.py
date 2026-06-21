"""Integration tests for PlaylistSyncBaseRepository.

The per-link base records the connector snapshot a link last reconciled to.
Verifies it round-trips, is one-per-link (upsert replaces), and reads back as
None before the first sync.
"""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.entities.playlist_sync_base import PlaylistSyncBase
from src.infrastructure.persistence.database.db_models import (
    DBConnectorPlaylist,
    DBPlaylist,
    DBPlaylistMapping,
)
from src.infrastructure.persistence.repositories.factories import get_unit_of_work


async def _make_link(session: AsyncSession) -> UUID:
    """Create the playlist → connector_playlist → mapping chain, return link id."""
    now = datetime.now(UTC)
    playlist = DBPlaylist(name="P", track_count=0, created_at=now, updated_at=now)
    connector_playlist = DBConnectorPlaylist(
        connector_name="spotify",
        connector_playlist_identifier="ext1",
        name="External",
        is_public=False,
        items=[],
        raw_metadata={},
        last_updated=now,
        created_at=now,
        updated_at=now,
    )
    session.add_all([playlist, connector_playlist])
    await session.flush()
    mapping = DBPlaylistMapping(
        user_id="default",
        playlist_id=playlist.id,
        connector_name="spotify",
        connector_playlist_id=connector_playlist.id,
        created_at=now,
        updated_at=now,
    )
    session.add(mapping)
    await session.flush()
    return mapping.id


def _base(link_id: UUID, *, snapshot: str) -> PlaylistSyncBase:
    return PlaylistSyncBase(
        link_id=link_id,
        user_id="default",
        connector_name="spotify",
        connector_playlist_identifier="ext1",
        base_snapshot_id=snapshot,
        base_taken_at=datetime.now(UTC),
    )


class TestSyncBaseRepository:
    async def test_none_before_first_sync(self, db_session: AsyncSession):
        link_id = await _make_link(db_session)
        repo = get_unit_of_work(db_session).get_playlist_sync_base_repository()
        assert await repo.get_for_link(link_id) is None

    async def test_upsert_then_get_round_trips(self, db_session: AsyncSession):
        link_id = await _make_link(db_session)
        repo = get_unit_of_work(db_session).get_playlist_sync_base_repository()

        await repo.upsert(_base(link_id, snapshot="snap1"))
        loaded = await repo.get_for_link(link_id)

        assert loaded is not None
        assert loaded.base_snapshot_id == "snap1"

    async def test_upsert_replaces_existing_base(self, db_session: AsyncSession):
        link_id = await _make_link(db_session)
        repo = get_unit_of_work(db_session).get_playlist_sync_base_repository()

        await repo.upsert(_base(link_id, snapshot="snap1"))
        await repo.upsert(_base(link_id, snapshot="snap2"))

        loaded = await repo.get_for_link(link_id)
        assert loaded is not None
        assert loaded.base_snapshot_id == "snap2"
