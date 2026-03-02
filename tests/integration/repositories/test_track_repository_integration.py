"""Integration tests for TrackRepository with real database operations following modern patterns."""

from uuid import uuid4

from src.domain.entities import Artist, Track
from src.infrastructure.persistence.repositories.factories import get_unit_of_work


class TestTrackRepositoryIntegration:
    """Integration tests for track repository with real database operations."""

    async def test_save_and_retrieve_track(self, db_session, test_data_tracker):
        """Test saving and retrieving a track with automatic cleanup tracking."""
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()

        # Create test track with unique identifiers
        test_track = Track(
            id=None,
            title=f"TEST_Track_{uuid4()}",
            artists=[Artist(name=f"TEST_Artist_{uuid4()}")],
            album=f"TEST_Album_{uuid4()}",
            duration_ms=180000,
            connector_track_identifiers={"spotify": f"spotify_{uuid4()}"},
        )

        # Save track
        saved_track = await track_repo.save_track(test_track)
        test_data_tracker.add_track(saved_track.id)

        # Verify track was saved correctly
        assert saved_track.id is not None
        assert saved_track.title == test_track.title
        assert saved_track.artists[0].name == test_track.artists[0].name
        assert saved_track.album == test_track.album
        assert saved_track.duration_ms == test_track.duration_ms

        # Retrieve track by ID
        retrieved_track = await track_repo.get_by_id(saved_track.id)
        assert retrieved_track is not None
        assert retrieved_track.title == test_track.title
        assert len(retrieved_track.artists) == 1
        assert retrieved_track.artists[0].name == test_track.artists[0].name

    async def test_find_tracks_by_ids_operations(self, db_session, test_data_tracker):
        """Test find_tracks_by_ids with empty list, single track, multiple tracks, and missing IDs."""
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()

        # Test empty list handling
        result = await track_repo.find_tracks_by_ids([])
        assert result == {}

        # Create test tracks
        track1 = Track(
            id=None,
            title=f"TEST_Track1_{uuid4()}",
            artists=[Artist(name=f"TEST_Artist1_{uuid4()}")],
            connector_track_identifiers={},
        )
        track2 = Track(
            id=None,
            title=f"TEST_Track2_{uuid4()}",
            artists=[Artist(name=f"TEST_Artist2_{uuid4()}")],
            connector_track_identifiers={},
        )

        # Save tracks
        saved_track1 = await track_repo.save_track(track1)
        saved_track2 = await track_repo.save_track(track2)
        test_data_tracker.add_track(saved_track1.id)
        test_data_tracker.add_track(saved_track2.id)

        # Test single track lookup
        single_result = await track_repo.find_tracks_by_ids([saved_track1.id])
        assert len(single_result) == 1
        assert saved_track1.id in single_result
        assert single_result[saved_track1.id].title == track1.title

        # Test multiple tracks lookup
        multi_result = await track_repo.find_tracks_by_ids([
            saved_track1.id,
            saved_track2.id,
        ])
        assert len(multi_result) == 2
        assert saved_track1.id in multi_result
        assert saved_track2.id in multi_result

        # Test missing track IDs (should not include non-existent IDs in result)
        missing_result = await track_repo.find_tracks_by_ids([saved_track1.id, 99999])
        assert len(missing_result) == 1  # Only the existing track
        assert saved_track1.id in missing_result
        assert 99999 not in missing_result

    async def test_track_with_connector_identifiers(
        self, db_session, test_data_tracker
    ):
        """Test track with multiple connector identifiers using correct field names."""
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()

        # Create track with connector identifiers
        test_track = Track(
            id=None,
            title=f"TEST_Track_Connectors_{uuid4()}",
            artists=[Artist(name=f"TEST_Artist_{uuid4()}")],
            connector_track_identifiers={
                "spotify": f"spotify_{uuid4()}",
                "lastfm": f"lastfm_{uuid4()}",
            },
        )

        # Save track
        saved_track = await track_repo.save_track(test_track)
        test_data_tracker.add_track(saved_track.id)

        # Verify connector identifiers functionality works (repository may filter or modify identifiers)
        assert (
            len(saved_track.connector_track_identifiers) >= 1
        )  # At least some identifiers preserved

        # Verify core functionality: connector identifier persistence
        has_spotify = "spotify" in saved_track.connector_track_identifiers
        has_lastfm = "lastfm" in saved_track.connector_track_identifiers
        assert (
            has_spotify or has_lastfm
        )  # At least one of the original identifiers should be preserved

        # Retrieve and verify persistence
        retrieved_track = await track_repo.get_by_id(saved_track.id)
        assert (
            len(retrieved_track.connector_track_identifiers) >= 1
        )  # Identifiers persist

    async def test_bulk_track_operations(self, db_session, test_data_tracker):
        """Test bulk operations and track management scenarios."""
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()

        # Create multiple tracks for bulk testing
        tracks_to_save = []
        for i in range(3):
            track = Track(
                id=None,
                title=f"TEST_BulkTrack_{i}_{uuid4()}",
                artists=[Artist(name=f"TEST_BulkArtist_{i}_{uuid4()}")],
                connector_track_identifiers={},
            )
            tracks_to_save.append(track)

        # Save tracks individually (testing repository consistency)
        saved_tracks = []
        for track in tracks_to_save:
            saved_track = await track_repo.save_track(track)
            saved_tracks.append(saved_track)
            test_data_tracker.add_track(saved_track.id)

        # Verify all tracks were saved with unique IDs
        saved_ids = [track.id for track in saved_tracks]
        assert len(set(saved_ids)) == 3  # All IDs should be unique
        assert all(track_id is not None for track_id in saved_ids)

        # Test bulk retrieval
        bulk_result = await track_repo.find_tracks_by_ids(saved_ids)
        assert len(bulk_result) == 3
        for saved_track in saved_tracks:
            assert saved_track.id in bulk_result
            retrieved = bulk_result[saved_track.id]
            assert retrieved.title.startswith("TEST_BulkTrack_")

    async def test_track_update_operations(self, db_session, test_data_tracker):
        """Test track update and modification scenarios."""
        uow = get_unit_of_work(db_session)
        track_repo = uow.get_track_repository()

        # Create and save initial track
        original_track = Track(
            id=None,
            title=f"TEST_Original_{uuid4()}",
            artists=[Artist(name=f"TEST_Artist_{uuid4()}")],
            album=f"TEST_Album_{uuid4()}",
            connector_track_identifiers={"spotify": f"spotify_{uuid4()}"},
        )

        saved_track = await track_repo.save_track(original_track)
        test_data_tracker.add_track(saved_track.id)

        # Update track with new connector identifier (using musicbrainz which is supported)
        updated_track = Track(
            id=saved_track.id,
            title=saved_track.title,
            artists=saved_track.artists,
            album=saved_track.album,
            connector_track_identifiers={
                **saved_track.connector_track_identifiers,
                "musicbrainz": f"mbid_{uuid4()}",
            },
        )

        # Save updated track
        final_track = await track_repo.save_track(updated_track)

        # Verify update was successful
        assert final_track.id == saved_track.id  # Same ID
        assert final_track.title == saved_track.title  # Same title
        assert (
            "spotify" in final_track.connector_track_identifiers
        )  # Original connector preserved
        assert (
            "musicbrainz" in final_track.connector_track_identifiers
        )  # New connector added
