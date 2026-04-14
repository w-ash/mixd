"""Integration tests for PlaylistLinkRepository with real database.

Tests the full repository → SQLAlchemy → SQLite cycle for playlist link CRUD,
including relationship loading via selectinload to DBConnectorPlaylist.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from src.domain.entities.playlist_link import PlaylistLink, SyncDirection, SyncStatus
from src.infrastructure.persistence.database.db_models import (
    DBConnectorPlaylist,
    DBPlaylist,
    DBPlaylistMapping,
)
from src.infrastructure.persistence.repositories.factories import get_unit_of_work


async def _setup_playlist_with_link(
    db_session,
    *,
    user_id: str = "default",
    connector_name: str = "spotify",
) -> tuple[UUID, UUID, UUID]:
    """Create a playlist + connector playlist + mapping, return (playlist_id, cp_id, mapping_id)."""
    uid = uuid4().hex[:8]

    db_playlist = DBPlaylist(
        name=f"Test Playlist {uid}",
        description="test",
        track_count=0,
        user_id=user_id,
    )
    db_playlist.playlist_tracks = []
    db_session.add(db_playlist)
    await db_session.flush()

    db_cp = DBConnectorPlaylist(
        connector_name=connector_name,
        connector_playlist_identifier=f"sp_{uid}",
        name=f"{connector_name} Playlist {uid}",
        description=None,
        owner="testuser",
        owner_id="user123",
        is_public=True,
        collaborative=False,
        follower_count=42,
        items=[],
        raw_metadata={},
        last_updated=datetime.now(UTC),
    )
    db_session.add(db_cp)
    await db_session.flush()

    db_mapping = DBPlaylistMapping(
        playlist_id=db_playlist.id,
        connector_name=connector_name,
        connector_playlist_id=db_cp.id,
        sync_direction="push",
        sync_status="synced",
        user_id=user_id,
    )
    db_session.add(db_mapping)
    await db_session.flush()
    await db_session.commit()

    return db_playlist.id, db_cp.id, db_mapping.id


class TestGetLinksForPlaylist:
    """get_links_for_playlist returns domain PlaylistLink entities."""

    async def test_returns_empty_for_unlinked_playlist(self, db_session):
        uow = get_unit_of_work(db_session)
        link_repo = uow.get_playlist_link_repository()

        links = await link_repo.get_links_for_playlist(uuid4())

        assert links == []

    async def test_returns_links_with_connector_details(self, db_session):
        playlist_id, _cp_id, mapping_id = await _setup_playlist_with_link(db_session)

        uow = get_unit_of_work(db_session)
        link_repo = uow.get_playlist_link_repository()

        links = await link_repo.get_links_for_playlist(playlist_id)

        assert len(links) == 1
        link = links[0]
        assert link.id == mapping_id
        assert link.playlist_id == playlist_id
        assert link.connector_name == "spotify"
        assert link.connector_playlist_identifier.startswith("sp_")
        assert link.connector_playlist_name is not None
        assert link.sync_direction == SyncDirection.PUSH
        assert link.sync_status == SyncStatus.SYNCED


class TestGetLink:
    """get_link returns a single PlaylistLink by ID."""

    async def test_returns_none_for_nonexistent(self, db_session):
        uow = get_unit_of_work(db_session)
        link_repo = uow.get_playlist_link_repository()

        result = await link_repo.get_link(uuid4())

        assert result is None

    async def test_returns_link_by_id(self, db_session):
        _playlist_id, _cp_id, mapping_id = await _setup_playlist_with_link(db_session)

        uow = get_unit_of_work(db_session)
        link_repo = uow.get_playlist_link_repository()

        link = await link_repo.get_link(mapping_id)

        assert link is not None
        assert link.id == mapping_id
        assert link.connector_name == "spotify"


class TestCreateLink:
    """create_link inserts a mapping row and returns a PlaylistLink."""

    async def test_creates_link_successfully(self, db_session):
        uid = uuid4().hex[:8]

        # Set up prerequisite rows
        db_playlist = DBPlaylist(
            name=f"Create Test {uid}", description=None, track_count=0
        )
        db_playlist.playlist_tracks = []
        db_session.add(db_playlist)
        await db_session.flush()

        db_cp = DBConnectorPlaylist(
            connector_name="spotify",
            connector_playlist_identifier=f"create_{uid}",
            name=f"Ext Playlist {uid}",
            description=None,
            owner=None,
            owner_id=None,
            is_public=True,
            collaborative=False,
            follower_count=None,
            items=[],
            raw_metadata={},
            last_updated=datetime.now(UTC),
        )
        db_session.add(db_cp)
        await db_session.flush()
        await db_session.commit()

        uow = get_unit_of_work(db_session)
        link_repo = uow.get_playlist_link_repository()

        new_link = PlaylistLink(
            playlist_id=db_playlist.id,
            connector_name="spotify",
            connector_playlist_identifier=f"create_{uid}",
            sync_direction=SyncDirection.PULL,
        )
        created = await link_repo.create_link(new_link)
        await db_session.commit()

        assert created.id is not None
        assert created.playlist_id == db_playlist.id
        assert created.connector_name == "spotify"
        assert created.sync_direction == SyncDirection.PULL

    async def test_raises_if_connector_playlist_missing(self, db_session):
        uow = get_unit_of_work(db_session)
        link_repo = uow.get_playlist_link_repository()

        link = PlaylistLink(
            playlist_id=uuid4(),
            connector_name="spotify",
            connector_playlist_identifier="nonexistent_id",
        )

        with pytest.raises(ValueError, match="ConnectorPlaylist not found"):
            await link_repo.create_link(link)


class TestUpdateSyncStatus:
    """update_sync_status modifies sync columns on existing links."""

    async def test_transition_to_syncing(self, db_session):
        _playlist_id, _cp_id, mapping_id = await _setup_playlist_with_link(db_session)

        uow = get_unit_of_work(db_session)
        link_repo = uow.get_playlist_link_repository()

        await link_repo.update_sync_status(mapping_id, SyncStatus.SYNCING)
        await db_session.commit()

        updated = await link_repo.get_link(mapping_id)
        assert updated is not None
        assert updated.sync_status == SyncStatus.SYNCING

    async def test_transition_to_error_with_message(self, db_session):
        _playlist_id, _cp_id, mapping_id = await _setup_playlist_with_link(db_session)

        uow = get_unit_of_work(db_session)
        link_repo = uow.get_playlist_link_repository()

        await link_repo.update_sync_status(
            mapping_id, SyncStatus.ERROR, error="Connection timeout"
        )
        await db_session.commit()

        updated = await link_repo.get_link(mapping_id)
        assert updated is not None
        assert updated.sync_status == SyncStatus.ERROR
        assert updated.last_sync_error == "Connection timeout"

    async def test_transition_to_synced_with_track_counts(self, db_session):
        _playlist_id, _cp_id, mapping_id = await _setup_playlist_with_link(db_session)

        uow = get_unit_of_work(db_session)
        link_repo = uow.get_playlist_link_repository()

        await link_repo.update_sync_status(
            mapping_id, SyncStatus.SYNCED, tracks_added=5, tracks_removed=2
        )
        await db_session.commit()

        updated = await link_repo.get_link(mapping_id)
        assert updated is not None
        assert updated.sync_status == SyncStatus.SYNCED
        assert updated.last_sync_tracks_added == 5
        assert updated.last_sync_tracks_removed == 2
        assert updated.last_synced is not None


class TestDeleteLink:
    """delete_link removes mapping row and returns success flag."""

    async def test_deletes_existing_link(self, db_session):
        _playlist_id, _cp_id, mapping_id = await _setup_playlist_with_link(db_session)

        uow = get_unit_of_work(db_session)
        link_repo = uow.get_playlist_link_repository()

        result = await link_repo.delete_link(mapping_id)
        await db_session.commit()

        assert result is True

        # Verify deleted
        link = await link_repo.get_link(mapping_id)
        assert link is None

    async def test_returns_false_for_nonexistent(self, db_session):
        uow = get_unit_of_work(db_session)
        link_repo = uow.get_playlist_link_repository()

        result = await link_repo.delete_link(uuid4())

        assert result is False


class TestListByUserConnector:
    """list_by_user_connector scopes links to (user_id, connector_name)."""

    async def test_returns_links_for_user(self, db_session):
        await _setup_playlist_with_link(db_session, user_id="alice")
        await _setup_playlist_with_link(db_session, user_id="alice")

        uow = get_unit_of_work(db_session)
        link_repo = uow.get_playlist_link_repository()

        links = await link_repo.list_by_user_connector("alice", "spotify")

        assert len(links) >= 2
        for link in links:
            assert link.connector_name == "spotify"

    async def test_excludes_other_users(self, db_session):
        _, _, alice_id = await _setup_playlist_with_link(db_session, user_id="alice")
        _, _, bob_id = await _setup_playlist_with_link(db_session, user_id="bob")

        uow = get_unit_of_work(db_session)
        link_repo = uow.get_playlist_link_repository()

        alice_links = await link_repo.list_by_user_connector("alice", "spotify")
        alice_ids = {link.id for link in alice_links}

        assert alice_id in alice_ids
        assert bob_id not in alice_ids

    async def test_excludes_other_connectors(self, db_session):
        _, _, sp_id = await _setup_playlist_with_link(
            db_session, user_id="alice", connector_name="spotify"
        )
        _, _, lf_id = await _setup_playlist_with_link(
            db_session, user_id="alice", connector_name="lastfm"
        )

        uow = get_unit_of_work(db_session)
        link_repo = uow.get_playlist_link_repository()

        spotify_links = await link_repo.list_by_user_connector("alice", "spotify")
        ids = {link.id for link in spotify_links}

        assert sp_id in ids
        assert lf_id not in ids


async def _seed_playlists_and_cps(db_session, *, count: int) -> list[tuple[UUID, str]]:
    """Seed N DBPlaylist + N DBConnectorPlaylist rows; return [(playlist_id, cp_identifier)]."""
    pairs: list[tuple[UUID, str]] = []
    uid = uuid4().hex[:8]
    for i in range(count):
        db_playlist = DBPlaylist(
            name=f"Batch {uid} {i}",
            description=None,
            track_count=0,
        )
        db_playlist.playlist_tracks = []
        db_session.add(db_playlist)
        await db_session.flush()

        cp_identifier = f"sp_batch_{uid}_{i}"
        db_cp = DBConnectorPlaylist(
            connector_name="spotify",
            connector_playlist_identifier=cp_identifier,
            name=f"Ext {uid} {i}",
            description=None,
            owner=None,
            owner_id=None,
            is_public=True,
            collaborative=False,
            follower_count=None,
            items=[],
            raw_metadata={},
            last_updated=datetime.now(UTC),
        )
        db_session.add(db_cp)
        await db_session.flush()
        pairs.append((db_playlist.id, cp_identifier))
    await db_session.commit()
    return pairs


class TestCreateLinksBatch:
    """Bulk create with ON CONFLICT DO NOTHING semantics."""

    async def test_inserts_all_new_links(self, db_session):
        pairs = await _seed_playlists_and_cps(db_session, count=3)

        uow = get_unit_of_work(db_session)
        link_repo = uow.get_playlist_link_repository()

        links = [
            PlaylistLink(
                playlist_id=pid,
                connector_name="spotify",
                connector_playlist_identifier=ident,
                sync_direction=SyncDirection.PULL,
                sync_status=SyncStatus.NEVER_SYNCED,
            )
            for pid, ident in pairs
        ]
        inserted = await link_repo.create_links_batch(links)
        await db_session.commit()

        assert len(inserted) == 3
        # Each canonical playlist now has exactly one link.
        for pid, _ in pairs:
            existing = await link_repo.get_links_for_playlist(pid)
            assert len(existing) == 1
            assert existing[0].sync_direction == SyncDirection.PULL

    async def test_on_conflict_returns_only_newly_inserted(self, db_session):
        pairs = await _seed_playlists_and_cps(db_session, count=2)

        uow = get_unit_of_work(db_session)
        link_repo = uow.get_playlist_link_repository()

        links = [
            PlaylistLink(
                playlist_id=pid,
                connector_name="spotify",
                connector_playlist_identifier=ident,
                sync_direction=SyncDirection.PULL,
            )
            for pid, ident in pairs
        ]
        first = await link_repo.create_links_batch(links)
        await db_session.commit()
        assert len(first) == 2

        # Replay — both exist now, ON CONFLICT DO NOTHING skips them.
        second = await link_repo.create_links_batch(links)
        await db_session.commit()
        assert second == []

    async def test_missing_connector_playlist_raises(self, db_session):
        uow = get_unit_of_work(db_session)
        link_repo = uow.get_playlist_link_repository()

        bogus = PlaylistLink(
            playlist_id=uuid4(),
            connector_name="spotify",
            connector_playlist_identifier=f"nonexistent_{uuid4().hex[:6]}",
            sync_direction=SyncDirection.PULL,
        )

        with pytest.raises(ValueError, match="ConnectorPlaylist not found"):
            _ = await link_repo.create_links_batch([bogus])

    async def test_empty_batch_short_circuits(self, db_session):
        uow = get_unit_of_work(db_session)
        link_repo = uow.get_playlist_link_repository()

        result = await link_repo.create_links_batch([])

        assert result == []
