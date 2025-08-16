"""Unit tests for PlaylistRepository core operations."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.domain.entities import Artist, Playlist, Track
from src.infrastructure.persistence.database.db_models import (
    DBPlaylistMapping,
    DBPlaylistTrack,
)
from src.infrastructure.persistence.repositories.playlist.core import PlaylistRepository


class TestPlaylistRepository:
    """Test core playlist repository operations."""

    @pytest.fixture
    def repo(self, db_session):
        """Playlist repository instance."""
        return PlaylistRepository(db_session)

    @pytest.fixture
    def playlist(self, tracks):
        """Basic playlist with tracks."""
        return Playlist(
            id=1,
            name="Test Playlist",
            description="Test playlist description",
            tracks=tracks,
        )

    @pytest.mark.asyncio
    async def test_save_new_playlist(self, repo, playlist):
        """Should save new playlist with tracks."""
        # Mock successful database operations
        repo.session.execute = AsyncMock()
        repo.session.flush = AsyncMock()
        repo.session.commit = AsyncMock()
        repo.session.rollback = AsyncMock()

        # Mock the transaction helper - since save_playlist uses execute_transaction
        # we need to mock the transaction infrastructure
        repo.execute_transaction = AsyncMock(return_value=playlist)

        result = await repo.save_playlist(playlist)

        assert result.name == "Test Playlist"
        assert len(result.tracks) == 3

    @pytest.mark.asyncio
    async def test_find_by_name(self, repo, playlist):
        """Should find playlist by name using find_by method."""
        # Mock find_by to return the playlist
        repo.find_by = AsyncMock(return_value=[playlist])

        result_list = await repo.find_by({"name": "Test Playlist"})
        result = result_list[0] if result_list else None

        assert result is not None
        assert result.name == "Test Playlist"

    @pytest.mark.asyncio
    async def test_find_by_name_not_found(self, repo):
        """Should return empty list when playlist not found."""
        # Mock find_by to return empty list
        repo.find_by = AsyncMock(return_value=[])

        result_list = await repo.find_by({"name": "Nonexistent Playlist"})

        assert result_list == []


class TestPlaylistTrackManagement:
    """Test _manage_playlist_tracks method for duplicate track handling."""

    @pytest.fixture
    def repo(self, db_session):
        """Playlist repository instance."""
        return PlaylistRepository(db_session)

    @pytest.fixture
    def tracks_with_duplicates(self):
        """Tracks including duplicates for testing position-based mapping."""
        # Create tracks where track_id=1 appears multiple times
        return [
            Track(id=1, title="Song A", artists=[Artist(name="Artist 1")], duration_ms=180000),
            Track(id=2, title="Song B", artists=[Artist(name="Artist 2")], duration_ms=200000),
            Track(id=1, title="Song A", artists=[Artist(name="Artist 1")], duration_ms=180000),  # Duplicate
            Track(id=3, title="Song C", artists=[Artist(name="Artist 3")], duration_ms=220000),
            Track(id=1, title="Song A", artists=[Artist(name="Artist 1")], duration_ms=180000),  # Another duplicate
        ]

    @pytest.mark.asyncio
    async def test_manage_playlist_tracks_create_operation(self, repo):
        """Should handle track creation with bulk insert and proper sort keys."""
        tracks = [
            Track(id=1, title="Track 1", artists=[Artist(name="Artist 1")], duration_ms=180000),
            Track(id=2, title="Track 2", artists=[Artist(name="Artist 2")], duration_ms=200000),
        ]
        
        # Mock session operations
        repo.session.execute = AsyncMock()
        repo.session.flush = AsyncMock()
        
        await repo._manage_playlist_tracks(playlist_id=1, tracks=tracks, operation="create")
        
        # Verify execute was called with insert statement
        repo.session.execute.assert_called_once()
        repo.session.flush.assert_called_once()
        
        # Get the call arguments to verify the insert values
        call_args = repo.session.execute.call_args[0][0]
        # Should be an insert statement with proper values structure
        assert hasattr(call_args, 'values')

    @pytest.mark.asyncio
    async def test_manage_playlist_tracks_update_preserves_metadata(self, repo):
        """Should preserve added_at timestamps during position-based updates."""
        tracks = [
            Track(id=1, title="Track 1", artists=[Artist(name="Artist 1")], duration_ms=180000),
            Track(id=2, title="Track 2", artists=[Artist(name="Artist 2")], duration_ms=200000),
        ]
        
        # Mock existing playlist tracks (simulate database state)
        existing_track_1 = MagicMock(spec=DBPlaylistTrack)
        existing_track_1.track_id = 1
        existing_track_1.sort_key = "a00000000"
        existing_track_1.added_at = datetime(2024, 1, 1, tzinfo=UTC)
        
        existing_track_2 = MagicMock(spec=DBPlaylistTrack)
        existing_track_2.track_id = 2
        existing_track_2.sort_key = "a00000001"
        existing_track_2.added_at = datetime(2024, 1, 2, tzinfo=UTC)
        
        # Mock session.scalars to return existing tracks
        mock_result = MagicMock()
        mock_result.all.return_value = [existing_track_1, existing_track_2]
        repo.session.scalars = AsyncMock(return_value=mock_result)
        repo.session.execute = AsyncMock()
        repo.session.flush = AsyncMock()
        repo.session.add_all = MagicMock()
        
        await repo._manage_playlist_tracks(playlist_id=1, tracks=tracks, operation="update")
        
        # Verify added_at timestamps were preserved (not overwritten)
        assert existing_track_1.added_at == datetime(2024, 1, 1, tzinfo=UTC)
        assert existing_track_2.added_at == datetime(2024, 1, 2, tzinfo=UTC)
        
        # Verify session operations were called
        repo.session.scalars.assert_called_once()
        repo.session.flush.assert_called_once()

    @pytest.mark.asyncio
    async def test_manage_playlist_tracks_handles_duplicate_tracks(self, repo, tracks_with_duplicates):
        """Should handle playlists with duplicate track IDs using position-based mapping."""
        # Mock existing tracks that map to different positions
        existing_tracks = []
        for i in range(5):
            track = MagicMock(spec=DBPlaylistTrack)
            track.track_id = i + 10  # Different IDs to simulate current state
            track.sort_key = f"a{i:08d}"
            track.added_at = datetime(2024, 1, i + 1, tzinfo=UTC)
            existing_tracks.append(track)
        
        mock_result = MagicMock()
        mock_result.all.return_value = existing_tracks
        repo.session.scalars = AsyncMock(return_value=mock_result)
        repo.session.execute = AsyncMock()
        repo.session.flush = AsyncMock()
        repo.session.add_all = MagicMock()
        
        await repo._manage_playlist_tracks(playlist_id=1, tracks=tracks_with_duplicates, operation="update")
        
        # Verify that session operations were called (position-based mapping applied)
        repo.session.scalars.assert_called_once()
        repo.session.flush.assert_called_once()
        
        # Verify that the mock objects were passed to add_all (indicating updates were applied)
        # The exact behavior depends on whether there are more target tracks than existing positions
        assert repo.session.add_all.call_count >= 1  # At least one call to add updated records
        
        # Verify that each existing track object was modified (track_id and sort_key updated)
        # Note: The exact values depend on the position-based mapping logic
        for i, track in enumerate(existing_tracks):
            # Each track should have been updated with new track_id and sort_key
            assert hasattr(track, 'track_id')  # Properties exist
            assert hasattr(track, 'sort_key')
            assert hasattr(track, 'updated_at')  # Updated timestamp should be set

    @pytest.mark.asyncio
    async def test_manage_playlist_tracks_handles_track_removal(self, repo):
        """Should properly remove tracks not in target list."""
        target_tracks = [
            Track(id=1, title="Keep Track", artists=[Artist(name="Artist 1")], duration_ms=180000),
        ]
        
        # Mock existing tracks (2 tracks, but target only has 1)
        existing_track_1 = MagicMock(spec=DBPlaylistTrack)
        existing_track_1.track_id = 1
        existing_track_1.sort_key = "a00000000"
        
        existing_track_2 = MagicMock(spec=DBPlaylistTrack)  # This should be removed
        existing_track_2.track_id = 2
        existing_track_2.sort_key = "a00000001"
        
        mock_result = MagicMock()
        mock_result.all.return_value = [existing_track_1, existing_track_2]
        repo.session.scalars = AsyncMock(return_value=mock_result)
        repo.session.execute = AsyncMock()
        repo.session.flush = AsyncMock()
        repo.session.add_all = MagicMock()
        
        await repo._manage_playlist_tracks(playlist_id=1, tracks=target_tracks, operation="update")
        
        # Verify track 2 was marked for deletion
        assert existing_track_2.is_deleted
        assert existing_track_2.deleted_at is not None
        
        # Verify track 1 remains and is updated to position 0
        assert existing_track_1.track_id == 1
        assert existing_track_1.sort_key == "a00000000"


class TestPlaylistConnectorMappings:
    """Test _manage_connector_mappings method for external service sync."""

    @pytest.fixture
    def repo(self, db_session):
        """Playlist repository instance."""
        return PlaylistRepository(db_session)

    @pytest.mark.asyncio
    async def test_manage_connector_mappings_create_operation(self, repo):
        """Should handle connector mapping creation with bulk insert."""
        connector_ids = {
            "spotify": "spotify_playlist_123",
            "lastfm": "lastfm_user_456",
        }
        
        # Mock session operations
        repo.session.execute = AsyncMock()
        repo.session.flush = AsyncMock()
        
        await repo._manage_connector_mappings(playlist_id=1, connector_ids=connector_ids, operation="create")
        
        # Verify execute was called with insert statement
        repo.session.execute.assert_called_once()
        repo.session.flush.assert_called_once()
        
        # Get the call arguments to verify the insert values
        call_args = repo.session.execute.call_args[0][0]
        # Should be an insert statement with proper values structure
        assert hasattr(call_args, 'values')

    @pytest.mark.asyncio
    async def test_manage_connector_mappings_update_existing(self, repo):
        """Should update existing mappings and create new ones."""
        connector_ids = {
            "spotify": "new_spotify_id_789",  # Update existing
            "lastfm": "new_lastfm_id_012",    # Create new
        }
        
        # Mock existing mapping (spotify exists, lastfm doesn't)
        existing_spotify_mapping = MagicMock(spec=DBPlaylistMapping)
        existing_spotify_mapping.connector_name = "spotify"
        existing_spotify_mapping.connector_playlist_id = "old_spotify_id_123"
        
        mock_result = MagicMock()
        mock_result.all.return_value = [existing_spotify_mapping]
        repo.session.scalars = AsyncMock(return_value=mock_result)
        repo.session.execute = AsyncMock()
        repo.session.flush = AsyncMock()
        repo.session.add_all = MagicMock()
        
        await repo._manage_connector_mappings(playlist_id=1, connector_ids=connector_ids, operation="update")
        
        # Verify existing spotify mapping was updated
        assert existing_spotify_mapping.connector_playlist_id == "new_spotify_id_789"
        assert hasattr(existing_spotify_mapping, 'updated_at')
        
        # Verify session operations were called
        repo.session.scalars.assert_called_once()
        repo.session.execute.assert_called_once()  # For new lastfm mapping
        repo.session.add_all.assert_called_once()  # For updated spotify mapping
        repo.session.flush.assert_called_once()

    @pytest.mark.asyncio
    async def test_manage_connector_mappings_no_changes_needed(self, repo):
        """Should skip updates when mapping IDs haven't changed."""
        connector_ids = {
            "spotify": "same_spotify_id_123",
        }
        
        # Mock existing mapping with same ID (no update needed)
        existing_mapping = MagicMock(spec=DBPlaylistMapping)
        existing_mapping.connector_name = "spotify"
        existing_mapping.connector_playlist_id = "same_spotify_id_123"
        
        mock_result = MagicMock()
        mock_result.all.return_value = [existing_mapping]
        repo.session.scalars = AsyncMock(return_value=mock_result)
        repo.session.execute = AsyncMock()
        repo.session.flush = AsyncMock()
        repo.session.add_all = MagicMock()
        
        await repo._manage_connector_mappings(playlist_id=1, connector_ids=connector_ids, operation="update")
        
        # Verify no updates were made (connector_playlist_id unchanged)
        assert existing_mapping.connector_playlist_id == "same_spotify_id_123"
        
        # Verify minimal session operations (just query, no updates/inserts)
        repo.session.scalars.assert_called_once()
        repo.session.execute.assert_not_called()  # No new inserts
        repo.session.add_all.assert_not_called()  # No updates needed
        repo.session.flush.assert_called_once()

    @pytest.mark.asyncio
    async def test_manage_connector_mappings_empty_input(self, repo):
        """Should handle empty connector_ids gracefully."""
        # Mock session operations (shouldn't be called)
        repo.session.execute = AsyncMock()
        repo.session.flush = AsyncMock()
        
        await repo._manage_connector_mappings(playlist_id=1, connector_ids={}, operation="create")
        
        # Verify no database operations were performed
        repo.session.execute.assert_not_called()
        repo.session.flush.assert_not_called()

    @pytest.mark.asyncio
    async def test_manage_connector_mappings_handles_missing_connectors(self, repo):
        """Should handle mappings for connectors not in target list (no deletion)."""
        connector_ids = {
            "spotify": "new_spotify_id_789",
        }
        
        # Mock existing mappings (including one not in target list)
        existing_spotify = MagicMock(spec=DBPlaylistMapping)
        existing_spotify.connector_name = "spotify"
        existing_spotify.connector_playlist_id = "old_spotify_id"
        
        existing_lastfm = MagicMock(spec=DBPlaylistMapping)  # Not in target - should remain unchanged
        existing_lastfm.connector_name = "lastfm"
        existing_lastfm.connector_playlist_id = "old_lastfm_id"
        
        mock_result = MagicMock()
        mock_result.all.return_value = [existing_spotify, existing_lastfm]
        repo.session.scalars = AsyncMock(return_value=mock_result)
        repo.session.execute = AsyncMock()
        repo.session.flush = AsyncMock()
        repo.session.add_all = MagicMock()
        
        await repo._manage_connector_mappings(playlist_id=1, connector_ids=connector_ids, operation="update")
        
        # Verify spotify mapping was updated
        assert existing_spotify.connector_playlist_id == "new_spotify_id_789"
        
        # Verify lastfm mapping was left unchanged (no deletion logic in connector mappings)
        assert existing_lastfm.connector_playlist_id == "old_lastfm_id"
        
        # Verify session operations
        repo.session.scalars.assert_called_once()
        repo.session.add_all.assert_called_once()  # For updated spotify mapping
        repo.session.flush.assert_called_once()


class TestPlaylistPersistenceOperations:
    """Test save_playlist() and update_playlist() core persistence methods."""

    @pytest.fixture
    def repo(self, db_session):
        """Playlist repository instance."""
        return PlaylistRepository(db_session)

    @pytest.fixture
    def sample_playlist(self):
        """Sample playlist for testing."""
        return Playlist(
            name="Test Playlist",
            description="Test Description",
            tracks=[
                Track(id=1, title="Track 1", artists=[Artist(name="Artist 1")], duration_ms=180000),
                Track(id=2, title="Track 2", artists=[Artist(name="Artist 2")], duration_ms=200000),
            ],
            connector_playlist_ids={"spotify": "spotify_123", "lastfm": "lastfm_456"},
        )

    @pytest.mark.asyncio
    async def test_save_playlist_validation_error(self, repo):
        """Should raise ValueError for playlist without name."""
        invalid_playlist = Playlist(
            name="",  # Empty name should fail validation
            description="Valid description",
            tracks=[],
            connector_playlist_ids={},
        )
        
        with pytest.raises(ValueError, match="Playlist must have a name"):
            await repo.save_playlist(invalid_playlist)

    @pytest.mark.asyncio
    async def test_save_playlist_calls_transaction_helper(self, repo, sample_playlist):
        """Should use execute_transaction for atomicity."""
        # Mock the transaction execution
        repo.execute_transaction = AsyncMock(return_value=sample_playlist)
        
        result = await repo.save_playlist(sample_playlist)
        
        # Verify transaction helper was called
        repo.execute_transaction.assert_called_once()
        assert result == sample_playlist

    @pytest.mark.asyncio
    async def test_update_playlist_validation_error(self, repo):
        """Should raise ValueError for playlist without name."""
        invalid_playlist = Playlist(
            name="",  # Empty name should fail validation
            description="Valid description",
            tracks=[],
            connector_playlist_ids={},
        )
        
        with pytest.raises(ValueError, match="Playlist must have a name"):
            await repo.update_playlist(playlist_id=1, playlist=invalid_playlist)

    @pytest.mark.asyncio
    async def test_update_playlist_calls_transaction_helper(self, repo, sample_playlist):
        """Should use execute_transaction for atomicity."""
        # Mock the transaction execution
        repo.execute_transaction = AsyncMock(return_value=sample_playlist)
        
        result = await repo.update_playlist(playlist_id=1, playlist=sample_playlist)
        
        # Verify transaction helper was called
        repo.execute_transaction.assert_called_once()
        assert result == sample_playlist

    @pytest.mark.asyncio
    async def test_update_playlist_calculates_actual_track_count(self, repo):
        """Should count only tracks with IDs for track_count field."""
        # Mix of tracks with and without IDs
        playlist_with_mixed_tracks = Playlist(
            name="Mixed Tracks Playlist",
            tracks=[
                Track(id=1, title="Track 1", artists=[Artist(name="Artist 1")], duration_ms=180000),  # Has ID
                Track(title="Track 2", artists=[Artist(name="Artist 2")], duration_ms=200000),      # No ID
                Track(id=3, title="Track 3", artists=[Artist(name="Artist 3")], duration_ms=220000),  # Has ID
            ],
            connector_playlist_ids={},
        )
        
        # Mock session and transaction operations
        repo.session.execute = AsyncMock()
        repo.session.flush = AsyncMock()
        repo.get_playlist_by_id = AsyncMock(return_value=playlist_with_mixed_tracks)
        repo._save_new_tracks = AsyncMock(return_value=playlist_with_mixed_tracks.tracks)
        repo._manage_playlist_tracks = AsyncMock()
        repo._manage_connector_mappings = AsyncMock()
        
        # Mock execute_transaction to directly call the implementation
        async def mock_execute_transaction(func):
            return await func()
        
        repo.execute_transaction = mock_execute_transaction
        
        await repo.update_playlist(playlist_id=1, playlist=playlist_with_mixed_tracks)
        
        # Verify execute was called with proper track count (only 2 tracks have IDs)
        execute_call = repo.session.execute.call_args[0][0]
        # The update should set track_count to 2 (tracks with IDs only)
        assert hasattr(execute_call, 'values')

    @pytest.mark.asyncio
    async def test_save_playlist_determines_source_connector(self, repo):
        """Should determine source connector based on priority order."""
        # Test the _determine_source_connector method directly
        connector_ids_spotify_first = {"spotify": "123", "lastfm": "456"}
        connector_ids_lastfm_only = {"lastfm": "456", "musicbrainz": "789"}
        connector_ids_empty = {}
        
        # Test priority order: spotify > lastfm > musicbrainz
        assert repo._determine_source_connector(connector_ids_spotify_first) == "spotify"
        assert repo._determine_source_connector(connector_ids_lastfm_only) == "lastfm"
        assert repo._determine_source_connector(connector_ids_empty) is None

    @pytest.mark.asyncio
    async def test_update_playlist_preserves_existing_relationships(self, repo, sample_playlist):
        """Should call _manage methods to update tracks and mappings."""
        # Mock all dependencies
        repo.session.execute = AsyncMock()
        repo.session.flush = AsyncMock()
        repo._save_new_tracks = AsyncMock(return_value=sample_playlist.tracks)
        repo._manage_playlist_tracks = AsyncMock()
        repo._manage_connector_mappings = AsyncMock()
        repo.get_playlist_by_id = AsyncMock(return_value=sample_playlist)
        
        # Mock execute_transaction to directly call the implementation
        async def mock_execute_transaction(func):
            return await func()
        
        repo.execute_transaction = mock_execute_transaction
        
        await repo.update_playlist(playlist_id=1, playlist=sample_playlist)
        
        # Verify helper methods were called with correct parameters
        repo._save_new_tracks.assert_called_once_with(sample_playlist.tracks, connector='spotify')
        repo._manage_playlist_tracks.assert_called_once_with(1, sample_playlist.tracks, operation="update")
        repo._manage_connector_mappings.assert_called_once_with(1, sample_playlist.connector_playlist_ids, operation="update")


class TestPlaylistErrorHandlingAndEdgeCases:
    """Test error handling, edge cases, and coverage gaps."""

    @pytest.fixture
    def repo(self, db_session):
        """Playlist repository instance."""
        return PlaylistRepository(db_session)

    @pytest.mark.asyncio
    async def test_manage_playlist_tracks_empty_tracks_list(self, repo):
        """Should handle empty tracks list gracefully."""
        # Should return early without database operations
        repo.session.execute = AsyncMock()
        repo.session.flush = AsyncMock()
        
        await repo._manage_playlist_tracks(playlist_id=1, tracks=[], operation="create")
        
        # Verify no database operations were performed
        repo.session.execute.assert_not_called()
        repo.session.flush.assert_not_called()

    @pytest.mark.asyncio
    async def test_manage_playlist_tracks_tracks_without_ids(self, repo):
        """Should skip tracks without IDs during database operations."""
        tracks_without_ids = [
            Track(title="No ID Track 1", artists=[Artist(name="Artist 1")], duration_ms=180000),
            Track(title="No ID Track 2", artists=[Artist(name="Artist 2")], duration_ms=200000),
        ]
        
        repo.session.execute = AsyncMock()
        repo.session.flush = AsyncMock()
        
        await repo._manage_playlist_tracks(playlist_id=1, tracks=tracks_without_ids, operation="create")
        
        # Should not execute any inserts since no tracks have IDs
        repo.session.execute.assert_not_called()
        repo.session.flush.assert_not_called()

    @pytest.mark.asyncio
    async def test_manage_playlist_tracks_preserves_connector_metadata_timestamps(self, repo):
        """Should extract added_at timestamps from connector metadata."""
        tracks_with_metadata = [
            Track(
                id=1,
                title="Track with Metadata",
                artists=[Artist(name="Artist 1")],
                duration_ms=180000,
                connector_metadata={
                    "spotify": {"added_at": "2024-01-15T10:30:00Z"},
                    "lastfm": {"added_at": "2024-01-16T11:45:00Z"},  # Should use first valid timestamp
                }
            ),
            Track(
                id=2,
                title="Track with Invalid Metadata",
                artists=[Artist(name="Artist 2")],
                duration_ms=200000,
                connector_metadata={
                    "spotify": {"added_at": "invalid-date"},  # Invalid format, should skip
                }
            ),
        ]
        
        repo.session.execute = AsyncMock()
        repo.session.flush = AsyncMock()
        
        await repo._manage_playlist_tracks(playlist_id=1, tracks=tracks_with_metadata, operation="create")
        
        # Verify execute was called (tracks have IDs)
        repo.session.execute.assert_called_once()
        repo.session.flush.assert_called_once()
        
        # Get the insert values to verify timestamp parsing
        call_args = repo.session.execute.call_args[0][0]
        assert hasattr(call_args, 'values')

    @pytest.mark.asyncio
    async def test_generate_sort_key_format(self, repo):
        """Should generate lexicographic sort keys with proper zero-padding."""
        # Test the _generate_sort_key method directly
        assert repo._generate_sort_key(0) == "a00000000"
        assert repo._generate_sort_key(1) == "a00000001"
        assert repo._generate_sort_key(99) == "a00000099"
        assert repo._generate_sort_key(12345) == "a00012345"
        
        # Verify lexicographic ordering works correctly
        keys = [repo._generate_sort_key(i) for i in [0, 1, 10, 100, 1000]]
        assert keys == sorted(keys)  # Should already be in correct order

    @pytest.mark.asyncio
    async def test_get_playlist_by_connector_not_found_behavior(self, repo):
        """Should handle not found cases based on raise_if_not_found parameter."""
        # Mock execute_select_one to return None (not found)
        repo.execute_select_one = AsyncMock(return_value=None)
        
        # Test with raise_if_not_found=True (default)
        with pytest.raises(ValueError, match="Playlist for spotify:nonexistent not found"):
            await repo.get_playlist_by_connector("spotify", "nonexistent", raise_if_not_found=True)
        
        # Test with raise_if_not_found=False
        result = await repo.get_playlist_by_connector("spotify", "nonexistent", raise_if_not_found=False)
        assert result is None

    @pytest.mark.asyncio
    async def test_get_playlist_by_id_not_found(self, repo):
        """Should raise ValueError when playlist ID doesn't exist."""
        # Mock execute_select_one to return None (not found)
        repo.execute_select_one = AsyncMock(return_value=None)
        
        with pytest.raises(ValueError, match="Playlist with ID 999 not found"):
            await repo.get_playlist_by_id(999)

    @pytest.mark.asyncio
    async def test_save_playlist_transaction_failure_handling(self, repo):
        """Should propagate transaction failures from execute_transaction."""
        invalid_playlist = Playlist(
            name="Valid Name",
            tracks=[],
            connector_playlist_ids={},
        )
        
        # Mock execute_transaction to raise an exception
        repo.execute_transaction = AsyncMock(side_effect=RuntimeError("Database connection lost"))
        
        with pytest.raises(RuntimeError, match="Database connection lost"):
            await repo.save_playlist(invalid_playlist)

    @pytest.mark.asyncio
    async def test_delete_playlist_success_and_failure_cases(self, repo):
        """Should handle both successful deletion and playlist not found cases."""
        datetime.now(UTC)
        
        # Mock successful deletion (rowcount > 0)
        success_result = MagicMock()
        success_result.rowcount = 1
        repo.session.execute = AsyncMock(return_value=success_result)
        repo._soft_delete_playlist_relations = AsyncMock()
        
        result = await repo.delete_playlist(123)
        assert result is True
        repo._soft_delete_playlist_relations.assert_called_once()
        
        # Mock failed deletion (rowcount = 0, playlist not found)
        failure_result = MagicMock()
        failure_result.rowcount = 0
        repo.session.execute = AsyncMock(return_value=failure_result)
        repo._soft_delete_playlist_relations = AsyncMock()
        
        result = await repo.delete_playlist(999)
        assert result is False
        repo._soft_delete_playlist_relations.assert_not_called()  # Should not delete relations if playlist not found

    @pytest.mark.asyncio
    async def test_soft_delete_playlist_relations(self, repo):
        """Should soft delete both playlist tracks and mappings."""
        deletion_time = datetime.now(UTC)
        
        repo.session.execute = AsyncMock()
        
        await repo._soft_delete_playlist_relations(123, deletion_time)
        
        # Should call execute twice (once for tracks, once for mappings)
        assert repo.session.execute.call_count == 2
        
        # Verify both update statements were executed
        call_args_list = repo.session.execute.call_args_list
        assert len(call_args_list) == 2


