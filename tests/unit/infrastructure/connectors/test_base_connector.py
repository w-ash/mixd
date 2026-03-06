"""Unit tests for BaseAPIConnector generic method delegation."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.domain.entities.playlist import ConnectorPlaylist
from src.domain.entities.track import Artist, ConnectorTrack
from src.infrastructure.connectors.base import BaseAPIConnector


class MockConnector(BaseAPIConnector):
    """Mock connector for testing BaseAPIConnector delegation."""

    @property
    def connector_name(self) -> str:
        return "test"

    def convert_track_to_connector(self, track_data: dict) -> ConnectorTrack:
        """Mock implementation of abstract method."""
        return ConnectorTrack(
            connector_name=self.connector_name,
            connector_track_id=track_data.get("id", "mock_id"),
            title=track_data.get("title", "Mock Title"),
            artists=[Artist(name=track_data.get("artist", "Mock Artist"))],
        )


class MockSpotifyConnector(BaseAPIConnector):
    """Mock Spotify connector for testing delegation."""

    @property
    def connector_name(self) -> str:
        return "spotify"

    def convert_track_to_connector(self, track_data: dict) -> ConnectorTrack:
        """Mock implementation of abstract method."""
        return ConnectorTrack(
            connector_name=self.connector_name,
            connector_track_id=track_data.get("id", "spotify_mock_id"),
            title=track_data.get("title", "Mock Spotify Title"),
            artists=[Artist(name=track_data.get("artist", "Mock Spotify Artist"))],
        )


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

        with pytest.raises(
            NotImplementedError,
            match="Playlist operations not supported by test connector",
        ):
            await connector.get_playlist("test_playlist_id")

    def test_get_connector_config_returns_service_specific_setting(self):
        """Test get_connector_config accesses service-specific configuration."""
        connector = MockSpotifyConnector()

        # Mock the settings call
        with patch("src.infrastructure.connectors.base.settings") as mock_settings:
            mock_settings.api.spotify.batch_size = 50

            result = connector.get_connector_config("BATCH_SIZE")

            assert result == 50

    def test_get_connector_config_returns_default_for_missing_setting(self):
        """Test get_connector_config returns default when setting doesn't exist."""
        connector = MockConnector()

        result = connector.get_connector_config("NONEXISTENT_SETTING", "default_value")

        assert result == "default_value"
