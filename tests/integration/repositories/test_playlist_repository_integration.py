"""Integration tests for PlaylistRepository with real database operations following modern patterns."""

from uuid import uuid4

import pytest

from src.domain.entities import Playlist
from src.domain.exceptions import NotFoundError
from src.infrastructure.persistence.repositories.factories import get_unit_of_work
from tests.fixtures import make_track


class TestPlaylistRepositoryIntegration:
    """Integration tests for playlist repository with real database operations."""

    async def test_save_and_retrieve_playlist(self, db_session):
        """Test saving and retrieving a playlist with automatic cleanup tracking."""
        uow = get_unit_of_work(db_session)
        playlist_repo = uow.get_playlist_repository()
        track_repo = uow.get_track_repository()

        track1 = make_track(
            title=f"TEST_Track_{uuid4()}",
            artist=f"TEST_Artist_{uuid4()}",
            connector_track_identifiers={},
        )
        track2 = make_track(
            title=f"TEST_Track_{uuid4()}",
            artist=f"TEST_Artist_{uuid4()}",
            connector_track_identifiers={},
        )

        saved_track1 = await track_repo.save_track(track1)
        saved_track2 = await track_repo.save_track(track2)

        test_playlist = Playlist.from_tracklist(
            name=f"TEST_Playlist_{uuid4()}",
            tracklist=[saved_track1, saved_track2],
            description="Test playlist for integration testing",
        )
        spotify_id = f"spotify_{uuid4()}"
        test_playlist = Playlist(
            id=test_playlist.id,
            name=test_playlist.name,
            description=test_playlist.description,
            entries=test_playlist.entries,
            connector_playlist_identifiers={"spotify": spotify_id},
        )

        saved_playlist = await playlist_repo.save_playlist(test_playlist)

        assert saved_playlist.id is not None
        assert saved_playlist.name == test_playlist.name
        assert len(saved_playlist.tracks) == 2

        retrieved_playlist = await playlist_repo.get_by_id(saved_playlist.id)
        assert retrieved_playlist is not None
        assert retrieved_playlist.name == test_playlist.name
        assert len(retrieved_playlist.tracks) == 2

    async def test_delete_playlist_hard_delete(self, db_session):
        """Test that playlist deletion is a hard delete with cascading cleanup."""
        uow = get_unit_of_work(db_session)
        playlist_repo = uow.get_playlist_repository()

        test_playlist = Playlist.from_tracklist(
            name=f"TEST_Playlist_Delete_{uuid4()}",
            tracklist=[],
        )
        test_playlist = Playlist(
            id=test_playlist.id,
            name=test_playlist.name,
            entries=test_playlist.entries,
            connector_playlist_identifiers={},
        )

        saved_playlist = await playlist_repo.save_playlist(test_playlist)

        retrieved = await playlist_repo.get_by_id(saved_playlist.id)
        assert retrieved is not None

        delete_result = await playlist_repo.delete_playlist(
            saved_playlist.id, user_id="default"
        )
        assert delete_result is True

        with pytest.raises(
            NotFoundError, match=f"Entity with ID {saved_playlist.id} not found"
        ):
            await playlist_repo.get_by_id(saved_playlist.id)

    async def test_playlist_with_connector_identifiers(self, db_session):
        """Test playlist with connector identifiers using correct field names."""
        uow = get_unit_of_work(db_session)
        playlist_repo = uow.get_playlist_repository()

        test_playlist = Playlist.from_tracklist(
            name=f"TEST_Playlist_Connectors_{uuid4()}",
            tracklist=[],
        )
        test_playlist = Playlist(
            id=test_playlist.id,
            name=test_playlist.name,
            entries=test_playlist.entries,
            connector_playlist_identifiers={
                "spotify": f"spotify_{uuid4()}",
                "lastfm": f"lastfm_{uuid4()}",
            },
        )

        saved_playlist = await playlist_repo.save_playlist(test_playlist)

        assert (
            saved_playlist.connector_playlist_identifiers
            == test_playlist.connector_playlist_identifiers
        )
        assert "spotify" in saved_playlist.connector_playlist_identifiers
        assert "lastfm" in saved_playlist.connector_playlist_identifiers

        retrieved_playlist = await playlist_repo.get_by_id(saved_playlist.id)
        assert (
            retrieved_playlist.connector_playlist_identifiers
            == test_playlist.connector_playlist_identifiers
        )

    async def test_playlist_track_management_operations(self, db_session):
        """Test advanced playlist track management: add, remove, reorder tracks."""
        uow = get_unit_of_work(db_session)
        playlist_repo = uow.get_playlist_repository()
        track_repo = uow.get_track_repository()

        tracks = []
        for i in range(4):
            track = make_track(
                title=f"TEST_Track_{i}_{uuid4()}",
                artist=f"TEST_Artist_{i}_{uuid4()}",
                connector_track_identifiers={},
            )
            saved_track = await track_repo.save_track(track)
            tracks.append(saved_track)

        initial_playlist = Playlist.from_tracklist(
            name=f"TEST_Playlist_Management_{uuid4()}",
            tracklist=tracks[:2],  # Start with first 2 tracks
        )
        initial_playlist = Playlist(
            id=initial_playlist.id,
            name=initial_playlist.name,
            entries=initial_playlist.entries,
            connector_playlist_identifiers={},
        )

        saved_playlist = await playlist_repo.save_playlist(initial_playlist)

        assert len(saved_playlist.tracks) == 2

        temp = Playlist.from_tracklist(
            name=saved_playlist.name,
            tracklist=tracks,  # All 4 tracks now
        )
        updated_playlist = Playlist(
            id=saved_playlist.id,
            name=temp.name,
            entries=temp.entries,
            connector_playlist_identifiers=saved_playlist.connector_playlist_identifiers,
        )

        await playlist_repo.save_playlist(updated_playlist)

        retrieved_playlist = await playlist_repo.get_by_id(saved_playlist.id)
        assert len(retrieved_playlist.tracks) == 4

        retrieved_track_ids = {track.id for track in retrieved_playlist.tracks}
        original_track_ids = {track.id for track in tracks}
        assert retrieved_track_ids == original_track_ids

    async def test_playlist_error_handling_scenarios(self, db_session):
        """Test playlist repository error handling and edge cases."""
        uow = get_unit_of_work(db_session)
        playlist_repo = uow.get_playlist_repository()

        nonexistent_id = uuid4()
        with pytest.raises(
            NotFoundError, match=f"Entity with ID {nonexistent_id} not found"
        ):
            await playlist_repo.get_by_id(nonexistent_id)

        delete_result = await playlist_repo.delete_playlist(uuid4(), user_id="default")
        assert delete_result is False

        empty_name_playlist = Playlist.from_tracklist(
            name="",  # Empty name
            tracklist=[],
        )
        empty_name_playlist = Playlist(
            id=empty_name_playlist.id,
            name=empty_name_playlist.name,
            entries=empty_name_playlist.entries,
            connector_playlist_identifiers={},
        )

        with pytest.raises(ValueError, match="must have a name"):
            await playlist_repo.save_playlist(empty_name_playlist)

    async def test_playlist_connector_mapping_creation(self, db_session):
        """Test playlist creation with multiple connector mappings."""
        uow = get_unit_of_work(db_session)
        playlist_repo = uow.get_playlist_repository()

        test_playlist = Playlist.from_tracklist(
            name=f"TEST_Playlist_Connectors_{uuid4()}",
            tracklist=[],
        )
        test_playlist = Playlist(
            id=test_playlist.id,
            name=test_playlist.name,
            entries=test_playlist.entries,
            connector_playlist_identifiers={
                "spotify": f"spotify_{uuid4()}",
                "lastfm": f"lastfm_{uuid4()}",
            },
        )

        saved_playlist = await playlist_repo.save_playlist(test_playlist)

        assert (
            saved_playlist.connector_playlist_identifiers
            == test_playlist.connector_playlist_identifiers
        )
        assert "spotify" in saved_playlist.connector_playlist_identifiers
        assert "lastfm" in saved_playlist.connector_playlist_identifiers

        retrieved_playlist = await playlist_repo.get_by_id(saved_playlist.id)
        assert (
            retrieved_playlist.connector_playlist_identifiers
            == test_playlist.connector_playlist_identifiers
        )

    async def test_playlist_duplicate_track_handling(self, db_session):
        """Test how playlists handle duplicate tracks (same track multiple times)."""
        uow = get_unit_of_work(db_session)
        playlist_repo = uow.get_playlist_repository()
        track_repo = uow.get_track_repository()

        test_track = make_track(
            title=f"TEST_Duplicate_Track_{uuid4()}",
            artist=f"TEST_Artist_{uuid4()}",
            connector_track_identifiers={},
        )

        saved_track = await track_repo.save_track(test_track)

        playlist_with_duplicates = Playlist.from_tracklist(
            name=f"TEST_Playlist_Duplicates_{uuid4()}",
            tracklist=[saved_track, saved_track, saved_track],  # Same track 3 times
        )
        playlist_with_duplicates = Playlist(
            id=playlist_with_duplicates.id,
            name=playlist_with_duplicates.name,
            entries=playlist_with_duplicates.entries,
            connector_playlist_identifiers={},
        )

        saved_playlist = await playlist_repo.save_playlist(playlist_with_duplicates)

        retrieved_playlist = await playlist_repo.get_by_id(saved_playlist.id)
        assert len(retrieved_playlist.tracks) == 3

        for track in retrieved_playlist.tracks:
            assert track.id == saved_track.id
            assert track.title == saved_track.title

    async def test_save_playlist_dedupes_on_connector_id_with_fresh_local_uuid(
        self, db_session
    ):
        """Saving a fresh-UUID Playlist with the same (connector, connector_playlist_id)
        as an existing one must take the update path, not collide on uq_connector_playlist.

        Regression for v0.7.8.11: workflow source nodes mint a fresh local UUID
        on every run. Without natural-identity dedup, the second run hits
        UniqueViolation on playlist_mappings.connector_playlist_id.
        """
        uow = get_unit_of_work(db_session)
        playlist_repo = uow.get_playlist_repository()

        spotify_id = f"spotify_{uuid4()}"
        user_id = f"test-user-{uuid4()}"
        playlist_name = f"TEST_NaturalIdentity_{uuid4()}"

        first = Playlist.from_tracklist(name=playlist_name, tracklist=[])
        first = Playlist(
            id=first.id,
            name=first.name,
            user_id=user_id,
            entries=first.entries,
            connector_playlist_identifiers={"spotify": spotify_id},
        )
        first_saved = await playlist_repo.save_playlist(first)

        # Second save with a fresh local UUID but identical connector identity —
        # this is exactly what a workflow source-node re-run produces.
        second = Playlist.from_tracklist(name=playlist_name, tracklist=[])
        second = Playlist(
            id=second.id,
            name=f"{playlist_name}_renamed",
            user_id=user_id,
            entries=second.entries,
            connector_playlist_identifiers={"spotify": spotify_id},
        )
        second_saved = await playlist_repo.save_playlist(second)

        # Natural-identity dedup: same local UUID on both saves
        assert second_saved.id == first_saved.id
        # Update path took effect (rename applied)
        assert second_saved.name == f"{playlist_name}_renamed"

    async def test_save_playlist_isolates_users_with_same_connector_id(
        self, db_session
    ):
        """Two users importing the same external playlist must each own a
        separate canonical row, mapped to the shared connector_playlist.

        Regression for v0.7.8.13: ``uq_connector_playlist`` was global, so
        User B's save would either crash on UniqueViolation or — under the
        v0.7.8.12 probe — route into User A's playlist row and silently
        overwrite it. The user-scoped constraint
        ``uq_user_connector_playlist`` lets both saves coexist.
        """
        uow = get_unit_of_work(db_session)
        playlist_repo = uow.get_playlist_repository()

        spotify_id = f"spotify_{uuid4()}"
        user_a = f"user-a-{uuid4()}"
        user_b = f"user-b-{uuid4()}"
        name = f"TEST_MultiUser_{uuid4()}"

        playlist_a = Playlist(
            name=f"{name}_A",
            user_id=user_a,
            connector_playlist_identifiers={"spotify": spotify_id},
        )
        saved_a = await playlist_repo.save_playlist(playlist_a)

        playlist_b = Playlist(
            name=f"{name}_B",
            user_id=user_b,
            connector_playlist_identifiers={"spotify": spotify_id},
        )
        saved_b = await playlist_repo.save_playlist(playlist_b)

        # Each user owns a distinct canonical row.
        assert saved_a.id != saved_b.id
        assert saved_a.user_id == user_a
        assert saved_b.user_id == user_b
        # Names preserved — User B's save did not overwrite User A's row.
        assert saved_a.name == f"{name}_A"
        assert saved_b.name == f"{name}_B"
        # Both mappings still point at the same shared external playlist.
        assert saved_a.connector_playlist_identifiers == {"spotify": spotify_id}
        assert saved_b.connector_playlist_identifiers == {"spotify": spotify_id}
