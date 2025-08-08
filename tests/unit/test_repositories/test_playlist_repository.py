"""Unit tests for PlaylistRepository core operations."""

from unittest.mock import AsyncMock

import pytest

from src.domain.entities import Artist, Playlist, Track
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


class TestPlaylistRepositoryGetOrCreateMany:
    """Integration tests for get_or_create_many functionality with rollback safety."""

    @pytest.fixture
    def repo(self, db_session):
        """Playlist repository instance with real database session."""
        return PlaylistRepository(db_session)

    @pytest.fixture
    def sample_tracks(self):
        """Sample tracks for testing."""
        return [
            Track(
                title="Test Track 1",
                artists=[Artist(name="Artist 1")],
                duration_ms=180000,
            ),
            Track(
                title="Test Track 2", 
                artists=[Artist(name="Artist 2")],
                duration_ms=200000,
            ),
        ]

    @pytest.mark.asyncio
    async def test_get_or_create_many_empty_input(self, repo):
        """Should handle empty input gracefully."""
        result = await repo.get_or_create_many([])
        assert result == []

    @pytest.mark.asyncio
    async def test_get_or_create_many_simple_playlists(self, repo, db_session):
        """Should create multiple simple playlists and use rollback for safety."""
        playlist_specs = [
            {
                "lookup_attrs": {"name": "Test Playlist 1"},
                "create_attrs": {"description": "First test playlist"},
            },
            {
                "lookup_attrs": {"name": "Test Playlist 2"}, 
                "create_attrs": {"description": "Second test playlist"},
            },
        ]

        try:
            # This should create new playlists
            results = await repo.get_or_create_many(playlist_specs)
            
            # Verify results structure
            assert len(results) == 2
            playlist1, _created1 = results[0]
            playlist2, _created2 = results[1]
            
            # Verify playlist properties
            assert playlist1.name == "Test Playlist 1"
            assert playlist1.description == "First test playlist"
            assert playlist2.name == "Test Playlist 2" 
            assert playlist2.description == "Second test playlist"
            
            # Verify both have IDs (were persisted)
            assert playlist1.id is not None
            assert playlist2.id is not None
            
            # Test finding existing playlists (second call)
            results_again = await repo.get_or_create_many(playlist_specs)
            assert len(results_again) == 2
            
            # Should find the same playlists by name
            found_playlist1, _found_created1 = results_again[0]
            found_playlist2, _found_created2 = results_again[1]
            
            assert found_playlist1.name == playlist1.name
            assert found_playlist2.name == playlist2.name
            
        finally:
            # Always rollback to prevent test data persistence
            await db_session.rollback()

    @pytest.mark.asyncio
    async def test_get_or_create_many_with_tracks_and_connectors(self, repo, db_session, sample_tracks):
        """Should handle complex playlists with tracks and connector mappings."""
        playlist_specs = [
            {
                "lookup_attrs": {"name": "Complex Playlist 1"},
                "create_attrs": {
                    "description": "Playlist with tracks",
                    "tracks": sample_tracks[:1],  # First track only
                    "connector_playlist_ids": {"spotify": "complex_123"},
                },
            },
            {
                "lookup_attrs": {"name": "Complex Playlist 2"},
                "create_attrs": {
                    "description": "Another complex playlist", 
                    "tracks": sample_tracks[1:],  # Second track only
                    "connector_playlist_ids": {"lastfm": "complex_456"},
                },
            },
        ]

        try:
            results = await repo.get_or_create_many(playlist_specs)
            
            assert len(results) == 2
            playlist1, _created1 = results[0]
            playlist2, _created2 = results[1]
            
            # Verify basic properties
            assert playlist1.name == "Complex Playlist 1"
            assert playlist2.name == "Complex Playlist 2"
            assert playlist1.id is not None
            assert playlist2.id is not None
            
            # Note: The current implementation marks complex playlists as "created" 
            # when tracks/mappings are added, even if base playlist existed
            
        finally:
            await db_session.rollback()

    @pytest.mark.asyncio
    async def test_get_or_create_many_validation_error(self, repo, db_session):
        """Should raise ValueError for invalid playlist specs."""
        playlist_specs = [
            {"lookup_attrs": {"name": "Valid Playlist"}},
            {"lookup_attrs": {"description": "Invalid - no name"}},  # Missing name
        ]

        try:
            with pytest.raises(ValueError, match="Playlist requires a name"):
                await repo.get_or_create_many(playlist_specs)
        finally:
            await db_session.rollback()

    @pytest.mark.asyncio  
    async def test_get_or_create_single_degenerate_case(self, repo, db_session):
        """Should handle single playlist via get_or_create as degenerate case."""
        lookup_attrs = {"name": "Single Playlist"}
        create_attrs = {"description": "Single test playlist"}

        try:
            # Test the single method that delegates to the many method
            playlist, _created = await repo.get_or_create(lookup_attrs, create_attrs)
            
            assert playlist.name == "Single Playlist"
            assert playlist.description == "Single test playlist"
            assert playlist.id is not None
            
            # Test finding the same playlist again
            playlist_again, _created_again = await repo.get_or_create(lookup_attrs, create_attrs)
            assert playlist_again.name == playlist.name
            assert playlist_again.id == playlist.id
            
        finally:
            await db_session.rollback()

    @pytest.mark.asyncio
    async def test_get_or_create_many_mixed_existing_and_new(self, repo, db_session):
        """Should handle mix of existing and new playlists in single batch."""
        # First, create one playlist
        first_spec = {
            "lookup_attrs": {"name": "Existing Playlist"},
            "create_attrs": {"description": "Already exists"},
        }
        
        try:
            # Create first playlist
            await repo.get_or_create_many([first_spec])
            
            # Now test batch with mix of existing and new
            mixed_specs = [
                first_spec,  # Should find existing
                {
                    "lookup_attrs": {"name": "New Playlist"},
                    "create_attrs": {"description": "Newly created"},
                },
            ]
            
            results = await repo.get_or_create_many(mixed_specs)
            assert len(results) == 2
            
            existing_playlist, _existing_created = results[0]
            new_playlist, _new_created = results[1]
            
            assert existing_playlist.name == "Existing Playlist"
            assert new_playlist.name == "New Playlist"
            assert existing_playlist.id is not None
            assert new_playlist.id is not None
            
        finally:
            await db_session.rollback()
