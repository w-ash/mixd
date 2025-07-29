"""Tests for LastFM likes functionality."""

from unittest.mock import MagicMock, patch

import pylast
import pytest

from src.infrastructure.connectors.lastfm import LastFMConnector


@pytest.mark.asyncio
async def test_love_track_success():
    """Test loving a track on LastFM successfully."""
    # Create mock LastFM objects
    mock_track = MagicMock()
    mock_user = MagicMock()

    # Create a mock LastFM client
    with patch("pylast.LastFMNetwork") as MockLastFM:
        # Configure the mock network
        mock_network = MagicMock()
        mock_network.get_user.return_value = mock_user
        mock_network.get_track.return_value = mock_track
        MockLastFM.return_value = mock_network

        # Create connector with the mock
        connector = LastFMConnector()
        connector.client = mock_network
        connector.client.username = "test_user"  # Mock authenticated client
        connector.lastfm_username = "test_user"

        # Call the method being tested
        success = await connector.love_track("Test Artist", "Test Track")

        # Verify correct methods were called
        mock_network.get_user.assert_called_once_with("test_user")
        mock_network.get_track.assert_called_once_with("Test Artist", "Test Track")
        mock_track.love.assert_called_once()

        # Should return True for success
        assert success is True


@pytest.mark.asyncio
async def test_love_track_no_user():
    """Test loving a track with no username configured."""
    # Create a mock LastFM client
    with patch("pylast.LastFMNetwork") as MockLastFM:
        # Configure the mock
        mock_network = MagicMock()
        MockLastFM.return_value = mock_network

        # Create connector with the mock but no username
        connector = LastFMConnector()
        connector.client = mock_network
        # Don't set client.username to simulate non-authenticated client
        connector.lastfm_username = None

        # Call the method being tested
        success = await connector.love_track("Test Artist", "Test Track")

        # Should return False since no username
        assert success is False

        # Verify no LastFM methods were called
        mock_network.get_user.assert_not_called()
        mock_network.get_track.assert_not_called()


@pytest.mark.asyncio
async def test_love_track_track_not_found():
    """Test loving a track that isn't found on LastFM."""
    # Create a mock LastFM client
    with patch("pylast.LastFMNetwork") as MockLastFM:
        # Configure the mock to raise a "not found" exception
        mock_network = MagicMock()
        mock_network.get_user.return_value = MagicMock()
        mock_network.get_track.side_effect = pylast.WSError(
            "network", "status", "details"
        )
        MockLastFM.return_value = mock_network

        # Create connector with the mock
        connector = LastFMConnector()
        connector.client = mock_network
        connector.client.username = "test_user"  # Mock authenticated client
        connector.lastfm_username = "test_user"

        # Call the method being tested
        success = await connector.love_track("Unknown Artist", "Unknown Track")

        # Should return False for failure
        assert success is False
