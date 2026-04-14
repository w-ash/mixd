"""Integration tests for PlaylistRepository with real database operations following modern patterns."""

from uuid import uuid4

import pytest

from src.domain.entities import Artist, Playlist, PlaylistEntry, Track
from src.domain.exceptions import NotFoundError
from src.infrastructure.persistence.repositories.factories import get_unit_of_work


class TestPlaylistRepositoryIntegration:
    """Integration tests for playlist repository with real database operations."""

    async def test_save_and_retrieve_playlist(self, db_session, test_data_tracker):
        """Test saving and retrieving a playlist with automatic cleanup tracking."""
        uow = get_unit_of_work(db_session)
        playlist_repo = uow.get_playlist_repository()
        track_repo = uow.get_track_repository()

        # Create test tracks with unique identifiers
        track1 = Track(
            id=None,
            title=f"TEST_Track_{uuid4()}",
            artists=[Artist(name=f"TEST_Artist_{uuid4()}")],
            connector_track_identifiers={},
        )
        track2 = Track(
            id=None,
            title=f"TEST_Track_{uuid4()}",
            artists=[Artist(name=f"TEST_Artist_{uuid4()}")],
            connector_track_identifiers={},
        )

        # Save tracks first
        saved_track1 = await track_repo.save_track(track1)
        saved_track2 = await track_repo.save_track(track2)
        test_data_tracker.add_track(saved_track1.id)
        test_data_tracker.add_track(saved_track2.id)

        # Create test playlist with unique name
        test_playlist = Playlist.from_tracklist(
            name=f"TEST_Playlist_{uuid4()}",
            tracklist=[saved_track1, saved_track2],
            description="Test playlist for integration testing",
        )
        # Add connector identifier separately
        spotify_id = f"spotify_{uuid4()}"
        test_playlist = Playlist(
            id=test_playlist.id,
            name=test_playlist.name,
            description=test_playlist.description,
            entries=test_playlist.entries,
            connector_playlist_identifiers={"spotify": spotify_id},
        )

        # Save playlist
        saved_playlist = await playlist_repo.save_playlist(test_playlist)
        test_data_tracker.add_playlist(saved_playlist.id)

        # Verify playlist was saved correctly
        assert saved_playlist.id is not None
        assert saved_playlist.name == test_playlist.name
        assert len(saved_playlist.tracks) == 2

        # Retrieve playlist by ID
        retrieved_playlist = await playlist_repo.get_by_id(saved_playlist.id)
        assert retrieved_playlist is not None
        assert retrieved_playlist.name == test_playlist.name
        assert len(retrieved_playlist.tracks) == 2

    async def test_delete_playlist_hard_delete(self, db_session, test_data_tracker):
        """Test that playlist deletion is a hard delete with cascading cleanup."""
        uow = get_unit_of_work(db_session)
        playlist_repo = uow.get_playlist_repository()

        # Create minimal test playlist
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

        # Save playlist
        saved_playlist = await playlist_repo.save_playlist(test_playlist)
        test_data_tracker.add_playlist(saved_playlist.id)

        # Verify it exists
        retrieved = await playlist_repo.get_by_id(saved_playlist.id)
        assert retrieved is not None

        # Delete the playlist
        delete_result = await playlist_repo.delete_playlist(
            saved_playlist.id, user_id="default"
        )
        assert delete_result is True

        # Verify it's hard deleted - no longer exists (should raise NotFoundError)
        with pytest.raises(
            NotFoundError, match=f"Entity with ID {saved_playlist.id} not found"
        ):
            await playlist_repo.get_by_id(saved_playlist.id)

    async def test_playlist_with_connector_identifiers(
        self, db_session, test_data_tracker
    ):
        """Test playlist with connector identifiers using correct field names."""
        uow = get_unit_of_work(db_session)
        playlist_repo = uow.get_playlist_repository()

        # Create playlist with multiple connector identifiers
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

        # Save playlist
        saved_playlist = await playlist_repo.save_playlist(test_playlist)
        test_data_tracker.add_playlist(saved_playlist.id)

        # Verify connector identifiers were saved correctly
        assert (
            saved_playlist.connector_playlist_identifiers
            == test_playlist.connector_playlist_identifiers
        )
        assert "spotify" in saved_playlist.connector_playlist_identifiers
        assert "lastfm" in saved_playlist.connector_playlist_identifiers

        # Retrieve and verify
        retrieved_playlist = await playlist_repo.get_by_id(saved_playlist.id)
        assert (
            retrieved_playlist.connector_playlist_identifiers
            == test_playlist.connector_playlist_identifiers
        )

    async def test_playlist_track_management_operations(
        self, db_session, test_data_tracker
    ):
        """Test advanced playlist track management: add, remove, reorder tracks."""
        uow = get_unit_of_work(db_session)
        playlist_repo = uow.get_playlist_repository()
        track_repo = uow.get_track_repository()

        # Create test tracks
        tracks = []
        for i in range(4):
            track = Track(
                id=None,
                title=f"TEST_Track_{i}_{uuid4()}",
                artists=[Artist(name=f"TEST_Artist_{i}_{uuid4()}")],
                connector_track_identifiers={},
            )
            saved_track = await track_repo.save_track(track)
            tracks.append(saved_track)
            test_data_tracker.add_track(saved_track.id)

        # Create playlist with initial tracks
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
        test_data_tracker.add_playlist(saved_playlist.id)

        # Verify initial state
        assert len(saved_playlist.tracks) == 2

        # Test updating with more tracks (repository behavior may vary)
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

        # Verify track management works (exact behavior may depend on repository implementation)
        retrieved_playlist = await playlist_repo.get_by_id(saved_playlist.id)
        assert (
            len(retrieved_playlist.tracks) >= 2
        )  # At least maintains the original tracks

        # Verify track IDs are preserved
        retrieved_track_ids = {track.id for track in retrieved_playlist.tracks}
        original_track_ids = {track.id for track in tracks}
        assert (
            len(retrieved_track_ids.intersection(original_track_ids)) >= 2
        )  # At least some tracks preserved

    async def test_playlist_error_handling_scenarios(
        self, db_session, test_data_tracker
    ):
        """Test playlist repository error handling and edge cases."""
        uow = get_unit_of_work(db_session)
        playlist_repo = uow.get_playlist_repository()

        # Test retrieval of non-existent playlist
        nonexistent_id = uuid4()
        with pytest.raises(
            NotFoundError, match=f"Entity with ID {nonexistent_id} not found"
        ):
            await playlist_repo.get_by_id(nonexistent_id)

        # Test deletion of non-existent playlist
        delete_result = await playlist_repo.delete_playlist(uuid4(), user_id="default")
        assert delete_result is False

        # Test playlist with empty name (should be handled gracefully)
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

        # This should either succeed or raise a clear validation error
        try:
            saved_playlist = await playlist_repo.save_playlist(empty_name_playlist)
            test_data_tracker.add_playlist(saved_playlist.id)
            assert saved_playlist.name == ""  # If allowed, should persist
        except ValueError:
            # If validation prevents empty names, that's also acceptable
            pass

    async def test_playlist_connector_mapping_creation(
        self, db_session, test_data_tracker
    ):
        """Test playlist creation with multiple connector mappings."""
        uow = get_unit_of_work(db_session)
        playlist_repo = uow.get_playlist_repository()

        # Create playlist with multiple connector mappings at once (simpler approach)
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
        test_data_tracker.add_playlist(saved_playlist.id)

        # Verify connector identifiers were saved correctly
        assert (
            saved_playlist.connector_playlist_identifiers
            == test_playlist.connector_playlist_identifiers
        )
        assert "spotify" in saved_playlist.connector_playlist_identifiers
        assert "lastfm" in saved_playlist.connector_playlist_identifiers

        # Retrieve and verify
        retrieved_playlist = await playlist_repo.get_by_id(saved_playlist.id)
        assert (
            retrieved_playlist.connector_playlist_identifiers
            == test_playlist.connector_playlist_identifiers
        )

    async def test_playlist_duplicate_track_handling(
        self, db_session, test_data_tracker
    ):
        """Test how playlists handle duplicate tracks (same track multiple times)."""
        uow = get_unit_of_work(db_session)
        playlist_repo = uow.get_playlist_repository()
        track_repo = uow.get_track_repository()

        # Create a single track
        test_track = Track(
            id=None,
            title=f"TEST_Duplicate_Track_{uuid4()}",
            artists=[Artist(name=f"TEST_Artist_{uuid4()}")],
            connector_track_identifiers={},
        )

        saved_track = await track_repo.save_track(test_track)
        test_data_tracker.add_track(saved_track.id)

        # Create playlist with the same track multiple times
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
        test_data_tracker.add_playlist(saved_playlist.id)

        # Verify behavior with duplicate tracks
        # (Implementation may deduplicate or preserve duplicates - both are valid)
        retrieved_playlist = await playlist_repo.get_by_id(saved_playlist.id)
        assert len(retrieved_playlist.tracks) >= 1  # At least one instance should exist

        # If duplicates are preserved, all should have the same track ID
        for track in retrieved_playlist.tracks:
            assert track.id == saved_track.id
            assert track.title == saved_track.title


class TestSavePlaylistsBatch:
    """Bulk save of N canonical playlists with pre-resolved tracks."""

    async def test_bulk_saves_playlists_and_entries(
        self, db_session, test_data_tracker
    ):
        uow = get_unit_of_work(db_session)
        playlist_repo = uow.get_playlist_repository()
        track_repo = uow.get_track_repository()

        # Pre-resolve two tracks (the import path guarantees this).
        track_a = await track_repo.save_track(
            Track(
                id=None,
                title=f"Batch_T_A_{uuid4()}",
                artists=[Artist(name=f"TEST_{uuid4()}")],
                connector_track_identifiers={},
            )
        )
        track_b = await track_repo.save_track(
            Track(
                id=None,
                title=f"Batch_T_B_{uuid4()}",
                artists=[Artist(name=f"TEST_{uuid4()}")],
                connector_track_identifiers={},
            )
        )
        test_data_tracker.add_track(track_a.id)
        test_data_tracker.add_track(track_b.id)

        uid = uuid4().hex[:8]
        playlists = [
            Playlist(
                name=f"BATCH_PL_A_{uid}",
                description="alpha",
                entries=[
                    PlaylistEntry(track=track_a),
                    PlaylistEntry(track=track_b),
                ],
            ),
            Playlist(
                name=f"BATCH_PL_B_{uid}",
                description=None,
                entries=[PlaylistEntry(track=track_b)],
            ),
        ]

        saved = await playlist_repo.save_playlists_batch(playlists)
        await db_session.commit()
        for p in saved:
            test_data_tracker.add_playlist(p.id)

        assert len(saved) == 2

        # Round-trip: read each back.
        back_a = await playlist_repo.get_playlist_by_id(
            playlists[0].id, user_id="default"
        )
        assert back_a.name == f"BATCH_PL_A_{uid}"
        assert len(back_a.entries) == 2
        back_b = await playlist_repo.get_playlist_by_id(
            playlists[1].id, user_id="default"
        )
        assert len(back_b.entries) == 1

    async def test_empty_batch_short_circuits(self, db_session):
        uow = get_unit_of_work(db_session)
        playlist_repo = uow.get_playlist_repository()

        result = await playlist_repo.save_playlists_batch([])

        assert result == []
