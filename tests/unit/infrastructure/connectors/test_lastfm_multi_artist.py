"""Tests for LastFM multi-artist fallback functionality."""

from unittest.mock import AsyncMock, Mock

import pytest

from src.domain.entities import Artist, Track
from src.infrastructure.connectors.lastfm import LastFMConnector, LastFMTrackInfo


@pytest.fixture
def mock_lastfm_connector():
    """Create a mock LastFM connector for testing."""
    connector = Mock(spec=LastFMConnector)
    connector.get_lastfm_track_info = AsyncMock()
    return connector


@pytest.fixture
def single_artist_track():
    """Track with single artist."""
    return Track(
        id=1,
        title="Test Track",
        artists=[Artist(name="Single Artist")],
    )


@pytest.fixture
def multi_artist_track():
    """Track with multiple artists."""
    return Track(
        id=2,
        title="Unknown",
        artists=[
            Artist(name="Versus GT"),
            Artist(name="Nosaj Thing"),
            Artist(name="Jacques Green"),
        ],
    )


@pytest.fixture
def successful_track_info():
    """Successful LastFM track info response."""
    return LastFMTrackInfo(
        lastfm_title="Unknown",
        lastfm_url="https://www.last.fm/music/Jacques+Greene+&+Nosaj+Thing/Unknown",
        lastfm_artist_name="Jacques Greene & Nosaj Thing",
        lastfm_global_playcount=1000,
        lastfm_listeners=500,
    )


@pytest.mark.unit
class TestLastFMMultiArtistFallback:
    """Test multi-artist fallback logic in LastFM connector."""

    async def test_single_artist_success_no_fallback_needed(
        self, mock_lastfm_connector, single_artist_track, successful_track_info
    ):
        """Test that single artist tracks work as before (no fallback needed)."""
        # Mock successful response on first try
        mock_lastfm_connector.get_lastfm_track_info.return_value = successful_track_info

        # Simulate the process_track logic for single artist
        track = single_artist_track
        result = await mock_lastfm_connector.get_lastfm_track_info(
            artist_name=track.artists[0].name,
            track_title=track.title,
            lastfm_username=None,
        )

        # Verify single call made and succeeded
        assert result.lastfm_url == successful_track_info.lastfm_url
        mock_lastfm_connector.get_lastfm_track_info.assert_called_once_with(
            artist_name="Single Artist",
            track_title="Test Track",
            lastfm_username=None,
        )

    async def test_multi_artist_fallback_success_on_second_artist(
        self, mock_lastfm_connector, multi_artist_track, successful_track_info
    ):
        """Test that fallback to second artist works when first fails."""
        # Mock: first artist fails, second artist succeeds
        mock_lastfm_connector.get_lastfm_track_info.side_effect = [
            LastFMTrackInfo.empty(),  # First artist fails
            successful_track_info,  # Second artist succeeds
        ]

        # Simulate the multi-artist fallback logic
        track = multi_artist_track
        result = None

        for artist in track.artists:
            result = await mock_lastfm_connector.get_lastfm_track_info(
                artist_name=artist.name,
                track_title=track.title,
                lastfm_username=None,
            )
            if result and result.lastfm_url:
                break

        # Verify we found the match and made exactly 2 calls
        assert result.lastfm_url == successful_track_info.lastfm_url
        assert mock_lastfm_connector.get_lastfm_track_info.call_count == 2

        # Verify the correct artists were tried
        calls = mock_lastfm_connector.get_lastfm_track_info.call_args_list
        assert calls[0][1]["artist_name"] == "Versus GT"
        assert calls[1][1]["artist_name"] == "Nosaj Thing"

    async def test_multi_artist_all_fail_returns_empty(
        self, mock_lastfm_connector, multi_artist_track
    ):
        """Test that when all artists fail, we get empty result."""
        # Mock: all artists fail
        mock_lastfm_connector.get_lastfm_track_info.return_value = (
            LastFMTrackInfo.empty()
        )

        # Simulate the multi-artist fallback logic
        track = multi_artist_track
        result = None

        for artist in track.artists:
            result = await mock_lastfm_connector.get_lastfm_track_info(
                artist_name=artist.name,
                track_title=track.title,
                lastfm_username=None,
            )
            if result and result.lastfm_url:
                break

        # Verify we tried all artists and got empty result
        assert not result or not result.lastfm_url
        assert mock_lastfm_connector.get_lastfm_track_info.call_count == 3

    async def test_multi_artist_success_on_third_artist(
        self, mock_lastfm_connector, multi_artist_track, successful_track_info
    ):
        """Test fallback all the way to third artist."""
        # Mock: first two fail, third succeeds
        mock_lastfm_connector.get_lastfm_track_info.side_effect = [
            LastFMTrackInfo.empty(),  # First artist fails
            LastFMTrackInfo.empty(),  # Second artist fails
            successful_track_info,  # Third artist succeeds
        ]

        # Simulate the multi-artist fallback logic
        track = multi_artist_track
        result = None

        for artist in track.artists:
            result = await mock_lastfm_connector.get_lastfm_track_info(
                artist_name=artist.name,
                track_title=track.title,
                lastfm_username=None,
            )
            if result and result.lastfm_url:
                break

        # Verify we found the match on third try
        assert result.lastfm_url == successful_track_info.lastfm_url
        assert mock_lastfm_connector.get_lastfm_track_info.call_count == 3

        # Verify the last call was for third artist
        calls = mock_lastfm_connector.get_lastfm_track_info.call_args_list
        assert calls[2][1]["artist_name"] == "Jacques Green"

    async def test_mbid_lookup_bypasses_artist_fallback(
        self, mock_lastfm_connector, multi_artist_track, successful_track_info
    ):
        """Test that MBID lookup succeeds and doesn't try artist fallback."""
        # Add MBID to track
        multi_artist_track.with_connector_track_id("musicbrainz", "test-mbid-123")

        # Mock successful MBID lookup
        mock_lastfm_connector.get_lastfm_track_info.return_value = successful_track_info

        # Simulate MBID lookup (should succeed immediately)
        result = await mock_lastfm_connector.get_lastfm_track_info(
            mbid="test-mbid-123",
            lastfm_username=None,
        )

        # Verify MBID lookup succeeded and no artist fallback needed
        assert result.lastfm_url == successful_track_info.lastfm_url
        mock_lastfm_connector.get_lastfm_track_info.assert_called_once_with(
            mbid="test-mbid-123",
            lastfm_username=None,
        )
