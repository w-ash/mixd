"""Unit tests for BaseAPIConnector generic method delegation."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.infrastructure.connectors.base_connector import BaseAPIConnector
from src.domain.entities.playlist import ConnectorPlaylist
from src.domain.entities.track import ConnectorTrack


class MockConnector(BaseAPIConnector):
    """Mock connector for testing BaseAPIConnector delegation."""
    
    @property
    def connector_name(self) -> str:
        return "test"


class MockSpotifyConnector(BaseAPIConnector):
    """Mock Spotify connector for testing delegation."""
    
    @property
    def connector_name(self) -> str:
        return "spotify"


class TestBaseAPIConnectorDelegation:
    """Test BaseAPIConnector generic method delegation patterns."""

    async def test_get_playlist_delegates_to_spotify_method(self):
        """Test get_playlist delegates to get_spotify_playlist for Spotify connector."""
        connector = MockSpotifyConnector()
        
        # Mock the specific method that should be called
        expected_playlist = MagicMock(spec=ConnectorPlaylist)
        connector.get_spotify_playlist = AsyncMock(return_value=expected_playlist)
        
        # Call generic method
        result = await connector.get_playlist("test_playlist_id")
        
        # Verify delegation occurred
        connector.get_spotify_playlist.assert_called_once_with("test_playlist_id")
        assert result == expected_playlist

    async def test_get_playlist_raises_for_unsupported_connector(self):
        """Test get_playlist raises NotImplementedError for unsupported connectors."""
        connector = MockConnector()  # Generic test connector
        
        with pytest.raises(NotImplementedError, match="Playlist operations not supported by test connector"):
            await connector.get_playlist("test_playlist_id")

    def test_convert_track_to_connector_delegates_to_spotify_function(self):
        """Test convert_track_to_connector delegates to Spotify conversion function."""
        connector = MockSpotifyConnector()
        
        test_track_data = {"id": "test_id", "name": "Test Track"}
        expected_connector_track = MagicMock(spec=ConnectorTrack)
        
        # Mock the conversion function that should be imported and called
        with patch('src.infrastructure.connectors.spotify.convert_spotify_track_to_connector') as mock_convert:
            mock_convert.return_value = expected_connector_track
            
            result = connector.convert_track_to_connector(test_track_data)
            
            # Verify delegation occurred
            mock_convert.assert_called_once_with(test_track_data)
            assert result == expected_connector_track

    def test_convert_track_to_connector_raises_for_unsupported_connector(self):
        """Test convert_track_to_connector raises NotImplementedError for unsupported connectors."""
        connector = MockConnector()  # Generic test connector
        
        with pytest.raises(NotImplementedError, match="Track conversion not supported by test connector"):
            connector.convert_track_to_connector({"id": "test"})