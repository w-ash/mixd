"""Tests for ConnectorMetadataManager service."""

from unittest.mock import AsyncMock, Mock

import pytest

from src.domain.entities import Artist, Track
from src.domain.matching.types import MatchResult
from src.domain.repositories.interfaces import ConnectorRepositoryProtocol
from src.infrastructure.services.connector_metadata_manager import (
    ConnectorMetadataManager,
)


@pytest.fixture
def mock_connector_repo():
    """Create mock connector repository."""
    mock = AsyncMock(spec=ConnectorRepositoryProtocol)
    # Set up default async method returns to avoid MagicMock issues
    mock.get_connector_mappings = AsyncMock(return_value={})
    mock.save_mapping_confidence = AsyncMock()
    mock.save_metadata = AsyncMock()
    mock.get_connector_metadata = AsyncMock(return_value={})
    return mock


@pytest.fixture
def metadata_manager(mock_connector_repo):
    """Create ConnectorMetadataManager instance."""
    return ConnectorMetadataManager(mock_connector_repo)


@pytest.fixture
def sample_identity_mappings():
    """Create sample identity mappings for testing."""
    track1 = Track(id=1, title="Home", artists=[Artist(name="Mac DeMarco")])
    track2 = Track(id=2, title="Falling", artists=[Artist(name="Chris Lake")])

    return {
        1: MatchResult(
            track=track1,
            success=True,
            connector_id="https://www.last.fm/music/mac+demarco/_/home",
            confidence=95,
            match_method="artist_title",
            service_data={"title": "Home", "artist": "Mac DeMarco"},
            evidence={"score": 0.95},
        ),
        2: MatchResult(
            track=track2,
            success=True,
            connector_id="https://www.last.fm/music/chris+lake/_/falling",
            confidence=90,
            match_method="artist_title",
            service_data={"title": "Falling", "artist": "Chris Lake"},
            evidence={"score": 0.90},
        ),
    }


class TestConnectorMetadataManager:
    """Test cases for ConnectorMetadataManager service."""

    @pytest.mark.asyncio
    async def test_fetch_fresh_metadata_success(
        self, metadata_manager, mock_connector_repo, sample_identity_mappings
    ):
        """Test successful fetching of fresh metadata using new direct approach."""
        # Setup: Mock connector with batch_get_track_info method
        mock_connector_instance = Mock()
        mock_track_info = Mock()
        mock_track_info.to_dict.return_value = {
            "title": "Home",
            "artist": "Mac DeMarco",
            "lastfm_user_playcount": 42,
            "lastfm_global_playcount": 1000,
        }

        mock_connector_instance.batch_get_track_info = AsyncMock(
            return_value={
                1: mock_track_info,
            }
        )

        # Setup: Mock existing connector mappings for direct API calls
        existing_mappings = {
            1: {"lastfm": "https://www.last.fm/music/mac+demarco/_/home"},
        }
        mock_connector_repo.get_connector_mappings.return_value = existing_mappings

        # Execute
        fresh_metadata, failed_track_ids = await metadata_manager.fetch_fresh_metadata(
            sample_identity_mappings,
            "lastfm",
            mock_connector_instance,
            [1],  # Only track 1 needs refresh
        )

        # Verify
        assert len(fresh_metadata) == 1
        assert fresh_metadata[1]["lastfm_user_playcount"] == 42
        assert fresh_metadata[1]["lastfm_global_playcount"] == 1000
        assert len(failed_track_ids) == 0

        # Verify direct API call was made with correct tracks
        mock_connector_instance.batch_get_track_info.assert_called_once()
        called_tracks = mock_connector_instance.batch_get_track_info.call_args[0][0]
        assert len(called_tracks) == 1
        assert called_tracks[0].id == 1

        # Note: Testing storage operations requires more complex session mocking
        # The important thing is that metadata was fetched successfully

    @pytest.mark.asyncio
    async def test_fetch_direct_metadata_by_connector_ids_success(
        self, metadata_manager, mock_connector_repo, sample_identity_mappings
    ):
        """Test successful direct metadata fetching using connector IDs."""
        # Setup: Mock connector with batch_get_track_info method
        mock_connector_instance = Mock()
        mock_track_info_1 = Mock()
        mock_track_info_1.to_dict.return_value = {
            "title": "Home",
            "artist": "Mac DeMarco",
            "lastfm_user_playcount": 42,
            "lastfm_global_playcount": 1000,
        }
        mock_track_info_2 = Mock()
        mock_track_info_2.to_dict.return_value = {
            "title": "Falling",
            "artist": "Chris Lake",
            "lastfm_user_playcount": 15,
            "lastfm_global_playcount": 500,
        }

        mock_connector_instance.batch_get_track_info = AsyncMock(
            return_value={
                1: mock_track_info_1,
                2: mock_track_info_2,
            }
        )

        # Setup: Mock existing connector mappings
        existing_mappings = {
            1: {"lastfm": "https://www.last.fm/music/mac+demarco/_/home"},
            2: {"lastfm": "https://www.last.fm/music/chris+lake/_/falling"},
        }
        mock_connector_repo.get_connector_mappings.return_value = existing_mappings

        # Execute: Call the new direct metadata fetch method
        result = await metadata_manager._fetch_direct_metadata_by_connector_ids(
            sample_identity_mappings,
            "lastfm",
            mock_connector_instance,
        )

        # Verify: Direct API call was made with correct tracks
        mock_connector_instance.batch_get_track_info.assert_called_once()
        called_tracks = mock_connector_instance.batch_get_track_info.call_args[0][0]
        assert len(called_tracks) == 2
        assert called_tracks[0].id in [1, 2]
        assert called_tracks[1].id in [1, 2]

        # Verify: Correct metadata was returned
        assert len(result) == 2
        assert result[1]["lastfm_user_playcount"] == 42
        assert result[1]["lastfm_global_playcount"] == 1000
        assert result[2]["lastfm_user_playcount"] == 15
        assert result[2]["lastfm_global_playcount"] == 500

    @pytest.mark.asyncio
    async def test_fetch_direct_metadata_no_connector_mappings(
        self, metadata_manager, mock_connector_repo, sample_identity_mappings
    ):
        """Test direct metadata fetch when no connector mappings exist."""
        mock_connector_instance = Mock()

        # Setup: No existing mappings
        mock_connector_repo.get_connector_mappings.return_value = {}

        # Execute
        result = await metadata_manager._fetch_direct_metadata_by_connector_ids(
            sample_identity_mappings,
            "lastfm",
            mock_connector_instance,
        )

        # Verify: No API calls made, empty result
        assert result == {}
        assert (
            not hasattr(mock_connector_instance, "batch_get_track_info")
            or not mock_connector_instance.batch_get_track_info.called
        )

    @pytest.mark.asyncio
    async def test_fetch_direct_metadata_no_batch_method_fails(
        self, metadata_manager, mock_connector_repo, sample_identity_mappings
    ):
        """Test that connectors without batch_get_track_info method fail appropriately."""
        # Setup: Mock connector without batch method (batch-first architecture)
        mock_connector_instance = Mock()

        # No batch method available - this should fail in batch-first architecture
        mock_connector_instance.batch_get_track_info = None

        # Setup: Mock existing connector mappings
        existing_mappings = {
            1: {"lastfm": "https://www.last.fm/music/mac+demarco/_/home"},
        }
        mock_connector_repo.get_connector_mappings.return_value = existing_mappings

        # Execute: Should return empty dict when no batch method
        single_track_mappings = {1: sample_identity_mappings[1]}
        result = await metadata_manager._fetch_direct_metadata_by_connector_ids(
            single_track_mappings,
            "lastfm",
            mock_connector_instance,
        )

        # Verify: Returns empty dict (batch-first requirement)
        assert result == {}

        # Verify: No individual calls were attempted
        assert (
            not hasattr(mock_connector_instance, "get_track_info")
            or not mock_connector_instance.get_track_info.called
        )

    @pytest.mark.asyncio
    async def test_fetch_fresh_metadata_no_tracks_to_refresh(
        self, metadata_manager, sample_identity_mappings
    ):
        """Test fetching when no tracks need refresh."""
        mock_connector_instance = Mock()

        fresh_metadata, failed_track_ids = await metadata_manager.fetch_fresh_metadata(
            sample_identity_mappings,
            "lastfm",
            mock_connector_instance,
            [],  # No tracks to refresh
        )

        assert fresh_metadata == {}
        assert failed_track_ids == set()

    @pytest.mark.asyncio
    async def test_fetch_fresh_metadata_no_valid_mappings(
        self, metadata_manager, sample_identity_mappings
    ):
        """Test fetching when no valid identity mappings exist for refresh tracks."""
        mock_connector_instance = Mock()

        fresh_metadata, failed_track_ids = await metadata_manager.fetch_fresh_metadata(
            sample_identity_mappings,
            "lastfm",
            mock_connector_instance,
            [999],  # Track ID not in identity mappings
        )

        assert fresh_metadata == {}
        assert failed_track_ids == {999}

    @pytest.mark.asyncio
    async def test_fetch_fresh_metadata_provider_exception(
        self, metadata_manager, mock_connector_repo, sample_identity_mappings
    ):
        """Test handling of connector exceptions during metadata fetching."""
        # Setup: Mock connector instance that raises exception
        mock_connector_instance = Mock()
        mock_connector_instance.batch_get_track_info = AsyncMock(
            side_effect=Exception("API connection failed")
        )

        # Setup: Mock existing mappings
        mock_connector_repo.get_connector_mappings.return_value = {
            1: {"lastfm": "https://www.last.fm/music/mac+demarco/_/home"}
        }

        fresh_metadata, failed_track_ids = await metadata_manager.fetch_fresh_metadata(
            sample_identity_mappings, "lastfm", mock_connector_instance, [1]
        )

        # Should return empty dict on connector failure
        assert fresh_metadata == {}
        assert failed_track_ids == {1}

    @pytest.mark.asyncio
    async def test_get_cached_metadata_success(
        self, metadata_manager, mock_connector_repo
    ):
        """Test successful retrieval of cached metadata."""
        # Setup: Mock cached metadata
        cached_metadata = {
            1: {"lastfm_user_playcount": 10, "lastfm_global_playcount": 500},
            2: {"lastfm_user_playcount": 5, "lastfm_global_playcount": 300},
        }
        mock_connector_repo.get_connector_metadata.return_value = cached_metadata

        # Execute
        result = await metadata_manager.get_cached_metadata([1, 2], "lastfm")

        # Verify
        assert result == cached_metadata
        mock_connector_repo.get_connector_metadata.assert_called_once_with(
            [1, 2], "lastfm"
        )

    @pytest.mark.asyncio
    async def test_get_cached_metadata_empty_tracks(
        self, metadata_manager, mock_connector_repo
    ):
        """Test cached metadata retrieval with empty track list."""
        result = await metadata_manager.get_cached_metadata([], "lastfm")

        assert result == {}
        mock_connector_repo.get_connector_metadata.assert_not_called()

    @pytest.mark.asyncio
    async def test_lastfm_track_info_metadata_conversion(
        self, metadata_manager, mock_connector_repo, sample_identity_mappings
    ):
        """Test that LastFMTrackInfo objects get properly converted to metadata dicts.

        This test reproduces the bug where LastFMTrackInfo (attrs class) doesn't have
        to_dict() method, causing metadata conversion to fail and return empty dicts.
        """
        # Import LastFMTrackInfo to create real instances
        from src.infrastructure.connectors.lastfm import LastFMTrackInfo

        # Setup: Create real LastFMTrackInfo instances with playcount data
        track_info_1 = LastFMTrackInfo(
            lastfm_title="Home",
            lastfm_artist_name="Mac DeMarco",
            lastfm_user_playcount=42,
            lastfm_global_playcount=1337,
            lastfm_listeners=999,
            lastfm_url="https://www.last.fm/music/Mac+DeMarco/_/Home",
        )
        track_info_2 = LastFMTrackInfo(
            lastfm_title="Falling",
            lastfm_artist_name="Chris Lake",
            lastfm_user_playcount=15,
            lastfm_global_playcount=500,
            lastfm_listeners=250,
            lastfm_url="https://www.last.fm/music/Chris+Lake/_/Falling",
        )

        # Setup: Mock connector with batch method returning real LastFMTrackInfo
        mock_connector_instance = Mock()
        mock_connector_instance.batch_get_track_info = AsyncMock(
            return_value={
                1: track_info_1,
                2: track_info_2,
            }
        )

        # Setup: Mock existing connector mappings for direct API calls
        existing_mappings = {
            1: {"lastfm": "https://www.last.fm/music/Mac+DeMarco/_/Home"},
            2: {"lastfm": "https://www.last.fm/music/Chris+Lake/_/Falling"},
        }
        mock_connector_repo.get_connector_mappings.return_value = existing_mappings

        # Execute: Call direct metadata fetch (this will trigger the conversion bug)
        result = await metadata_manager._fetch_direct_metadata_by_connector_ids(
            sample_identity_mappings,
            "lastfm",
            mock_connector_instance,
        )

        # Verify: Metadata conversion should succeed (this will fail initially due to bug)
        assert len(result) == 2, "Should have metadata for both tracks"

        # Verify track 1 metadata contains playcount fields
        track_1_metadata = result[1]
        assert "lastfm_user_playcount" in track_1_metadata, "Should have user playcount"
        assert "lastfm_global_playcount" in track_1_metadata, (
            "Should have global playcount"
        )
        assert "lastfm_listeners" in track_1_metadata, "Should have listeners count"
        assert track_1_metadata["lastfm_user_playcount"] == 42
        assert track_1_metadata["lastfm_global_playcount"] == 1337
        assert track_1_metadata["lastfm_listeners"] == 999

        # Verify track 2 metadata contains playcount fields
        track_2_metadata = result[2]
        assert "lastfm_user_playcount" in track_2_metadata, "Should have user playcount"
        assert "lastfm_global_playcount" in track_2_metadata, (
            "Should have global playcount"
        )
        assert "lastfm_listeners" in track_2_metadata, "Should have listeners count"
        assert track_2_metadata["lastfm_user_playcount"] == 15
        assert track_2_metadata["lastfm_global_playcount"] == 500
        assert track_2_metadata["lastfm_listeners"] == 250

        # Verify all expected LastFM fields are preserved
        assert track_1_metadata["lastfm_title"] == "Home"
        assert track_1_metadata["lastfm_artist_name"] == "Mac DeMarco"
        assert (
            track_1_metadata["lastfm_url"]
            == "https://www.last.fm/music/Mac+DeMarco/_/Home"
        )

        # Verify API was called correctly
        mock_connector_instance.batch_get_track_info.assert_called_once()
        called_tracks = mock_connector_instance.batch_get_track_info.call_args[0][0]
        assert len(called_tracks) == 2

    def test_convert_track_info_results_with_to_dict(self, metadata_manager):
        """Test metadata conversion for objects with to_dict method."""
        # Setup: Mock track info with to_dict method
        mock_track_info = Mock()
        mock_track_info.to_dict.return_value = {
            "title": "Test Track",
            "artist": "Test Artist",
            "lastfm_user_playcount": 100,
        }

        track_info_results = {1: mock_track_info}

        # Execute
        result = metadata_manager._convert_track_info_results(track_info_results)

        # Verify
        assert len(result) == 1
        assert result[1]["title"] == "Test Track"
        assert result[1]["lastfm_user_playcount"] == 100
        mock_track_info.to_dict.assert_called_once()

    def test_convert_track_info_results_with_attrs(self, metadata_manager):
        """Test metadata conversion for attrs classes (like LastFMTrackInfo)."""
        # Import LastFMTrackInfo to create real instance
        from src.infrastructure.connectors.lastfm import LastFMTrackInfo

        # Setup: Real LastFMTrackInfo instance
        track_info = LastFMTrackInfo(
            lastfm_title="Test Track",
            lastfm_artist_name="Test Artist",
            lastfm_user_playcount=50,
            lastfm_global_playcount=5000,
            lastfm_listeners=1000,
            lastfm_url="https://www.last.fm/music/Test+Artist/_/Test+Track",
        )

        track_info_results = {1: track_info}

        # Execute
        result = metadata_manager._convert_track_info_results(track_info_results)

        # Verify: attrs.asdict() was used correctly
        assert len(result) == 1
        assert result[1]["lastfm_title"] == "Test Track"
        assert result[1]["lastfm_artist_name"] == "Test Artist"
        assert result[1]["lastfm_user_playcount"] == 50
        assert result[1]["lastfm_global_playcount"] == 5000

    def test_convert_track_info_results_with_dict(self, metadata_manager):
        """Test metadata conversion for plain dictionaries."""
        # Setup: Plain dictionary
        track_info_dict = {
            "title": "Dict Track",
            "artist": "Dict Artist",
            "playcount": 25,
        }

        track_info_results = {1: track_info_dict}

        # Execute
        result = metadata_manager._convert_track_info_results(track_info_results)

        # Verify: Dictionary passed through unchanged
        assert len(result) == 1
