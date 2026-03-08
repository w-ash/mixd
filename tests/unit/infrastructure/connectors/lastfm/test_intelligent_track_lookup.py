"""Unit tests for Last.FM intelligent track lookup with fallback strategies.

Tests verify:
1. MBID → artist/title fallback behavior
2. Multi-artist fallback strategy
3. Connection error vs not-found error handling

All tests use mocked Last.FM API calls — no network or database dependencies.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.domain.entities import Artist, Track
from src.infrastructure.connectors.lastfm.conversions import LastFMTrackInfo
from src.infrastructure.connectors.lastfm.models import LastFMAPIError
from src.infrastructure.connectors.lastfm.operations import LastFMOperations


@pytest.fixture
def lastfm_operations():
    """Create Last.FM operations instance with mocked client."""
    mock_client = MagicMock()
    return LastFMOperations(client=mock_client)


@pytest.fixture
def sample_track():
    """Create a sample track for testing."""
    return Track(
        id=1,
        title="Test Song",
        artists=[Artist(name="Test Artist")],
    )


@pytest.fixture
def multi_artist_track():
    """Create a multi-artist track for testing."""
    return Track(
        id=2,
        title="Collaboration",
        artists=[
            Artist(name="Artist One"),
            Artist(name="Artist Two"),
            Artist(name="Artist Three"),
        ],
    )


class TestMBIDFallback:
    """Tests for MBID lookup with artist/title fallback."""

    async def test_mbid_lookup_succeeds_no_fallback_needed(
        self, lastfm_operations, sample_track
    ):
        """MBID lookup succeeds on first try, no artist/title fallback needed."""
        track_with_mbid = sample_track.with_connector_metadata(
            "lastfm", {"lastfm_mbid": "test-mbid-123"}
        )

        mock_info = LastFMTrackInfo(
            lastfm_title="Test Song",
            lastfm_artist_name="Test Artist",
            lastfm_url="https://last.fm/music/test",
        )
        lastfm_operations.client.get_track_info_comprehensive_by_mbid = AsyncMock(
            return_value=mock_info
        )

        result = await lastfm_operations.get_track_info_intelligent(track_with_mbid)

        assert result is not None
        assert result.lastfm_title == "Test Song"
        assert lastfm_operations.client.get_track_info_comprehensive_by_mbid.called
        assert not lastfm_operations.client.get_track_info_comprehensive.called

    async def test_mbid_fails_fallback_to_artist_title_succeeds(
        self, lastfm_operations, sample_track
    ):
        """MBID lookup fails, artist/title fallback succeeds."""
        track_with_mbid = sample_track.with_connector_metadata(
            "lastfm", {"lastfm_mbid": "nonexistent-mbid"}
        )

        lastfm_operations.client.get_track_info_comprehensive_by_mbid = AsyncMock(
            return_value=None
        )

        mock_info = LastFMTrackInfo(
            lastfm_title="Test Song",
            lastfm_artist_name="Test Artist",
            lastfm_url="https://last.fm/music/test",
        )
        lastfm_operations.client.get_track_info_comprehensive = AsyncMock(
            return_value=mock_info
        )

        result = await lastfm_operations.get_track_info_intelligent(track_with_mbid)

        assert result is not None
        assert result.lastfm_title == "Test Song"
        assert lastfm_operations.client.get_track_info_comprehensive_by_mbid.called
        assert lastfm_operations.client.get_track_info_comprehensive.called

    async def test_no_mbid_goes_straight_to_artist_title(
        self, lastfm_operations, sample_track
    ):
        """Track with no MBID goes straight to artist/title lookup."""
        mock_info = LastFMTrackInfo(
            lastfm_title="Test Song",
            lastfm_artist_name="Test Artist",
            lastfm_url="https://last.fm/music/test",
        )
        lastfm_operations.client.get_track_info_comprehensive = AsyncMock(
            return_value=mock_info
        )

        result = await lastfm_operations.get_track_info_intelligent(sample_track)

        assert result is not None
        assert result.lastfm_title == "Test Song"
        assert not lastfm_operations.client.get_track_info_comprehensive_by_mbid.called
        assert lastfm_operations.client.get_track_info_comprehensive.called

    async def test_both_mbid_and_artist_title_fail(
        self, lastfm_operations, sample_track
    ):
        """Both MBID and artist/title lookups fail, returns empty."""
        track_with_mbid = sample_track.with_connector_metadata(
            "lastfm", {"lastfm_mbid": "nonexistent-mbid"}
        )

        lastfm_operations.client.get_track_info_comprehensive_by_mbid = AsyncMock(
            return_value=None
        )
        lastfm_operations.client.get_track_info_comprehensive = AsyncMock(
            return_value=None
        )

        result = await lastfm_operations.get_track_info_intelligent(track_with_mbid)

        assert result is not None
        assert result.lastfm_title is None
        assert lastfm_operations.client.get_track_info_comprehensive_by_mbid.called
        assert lastfm_operations.client.get_track_info_comprehensive.called


class TestMultiArtistFallback:
    """Tests for multi-artist track lookup with per-artist fallback."""

    async def test_first_artist_succeeds(self, lastfm_operations, multi_artist_track):
        """Multi-artist track where first artist matches."""
        mock_info = LastFMTrackInfo(
            lastfm_title="Collaboration",
            lastfm_artist_name="Artist One",
            lastfm_url="https://last.fm/music/test",
        )
        mock_get_track_info = AsyncMock(return_value=mock_info)
        lastfm_operations.client.get_track_info_comprehensive = mock_get_track_info

        result = await lastfm_operations.get_track_info_intelligent(multi_artist_track)

        assert result is not None
        assert result.lastfm_title == "Collaboration"
        assert result.lastfm_artist_name == "Artist One"
        assert mock_get_track_info.call_count == 1
        mock_get_track_info.assert_called_once_with("Artist One", "Collaboration")

    async def test_second_artist_succeeds(self, lastfm_operations, multi_artist_track):
        """Multi-artist track where only second artist matches."""
        call_count = 0

        async def mock_get_track_info_func(artist: str, title: str):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return None
            return LastFMTrackInfo(
                lastfm_title="Collaboration",
                lastfm_artist_name=artist,
                lastfm_url="https://last.fm/music/test",
            )

        mock_get_track_info = AsyncMock(side_effect=mock_get_track_info_func)
        lastfm_operations.client.get_track_info_comprehensive = mock_get_track_info

        result = await lastfm_operations.get_track_info_intelligent(multi_artist_track)

        assert result is not None
        assert result.lastfm_title == "Collaboration"
        assert result.lastfm_artist_name == "Artist Two"
        assert mock_get_track_info.call_count == 2

    async def test_all_artists_fail(self, lastfm_operations, multi_artist_track):
        """Multi-artist track where no artist matches."""
        mock_get_track_info = AsyncMock(return_value=None)
        lastfm_operations.client.get_track_info_comprehensive = mock_get_track_info

        result = await lastfm_operations.get_track_info_intelligent(multi_artist_track)

        assert result is not None
        assert result.lastfm_title is None
        assert mock_get_track_info.call_count == 3

        calls = mock_get_track_info.call_args_list
        assert calls[0][0] == ("Artist One", "Collaboration")
        assert calls[1][0] == ("Artist Two", "Collaboration")
        assert calls[2][0] == ("Artist Three", "Collaboration")

    async def test_single_artist_no_multi_artist_fallback(
        self, lastfm_operations, sample_track
    ):
        """Single-artist track with no match has nothing to fall back to."""
        mock_get_track_info = AsyncMock(return_value=None)
        lastfm_operations.client.get_track_info_comprehensive = mock_get_track_info

        result = await lastfm_operations.get_track_info_intelligent(sample_track)

        assert result is not None
        assert result.lastfm_title is None
        assert mock_get_track_info.call_count == 1


class TestErrorHandling:
    """Tests for error handling during intelligent track lookup."""

    async def test_connection_error_falls_back_to_artist_title(
        self, lastfm_operations, sample_track
    ):
        """Connection error on MBID lookup falls back to artist/title."""
        track_with_mbid = sample_track.with_connector_metadata(
            "lastfm", {"lastfm_mbid": "test-mbid-123"}
        )

        lastfm_operations.client.get_track_info_comprehensive_by_mbid = AsyncMock(
            side_effect=LastFMAPIError(11, "Service Offline - Try again later")
        )

        mock_info = LastFMTrackInfo(
            lastfm_title="Test Song",
            lastfm_artist_name="Test Artist",
            lastfm_url="https://last.fm/music/test",
        )
        lastfm_operations.client.get_track_info_comprehensive = AsyncMock(
            return_value=mock_info
        )

        result = await lastfm_operations.get_track_info_intelligent(track_with_mbid)

        assert result is not None
        assert result.lastfm_title == "Test Song"
        assert lastfm_operations.client.get_track_info_comprehensive_by_mbid.called
        assert lastfm_operations.client.get_track_info_comprehensive.called

    async def test_connection_error_propagates_when_no_fallback(
        self, lastfm_operations, sample_track
    ):
        """Connection error with no fallback returns empty result."""
        lastfm_operations.client.get_track_info_comprehensive = AsyncMock(
            side_effect=LastFMAPIError(11, "Service Offline - Try again later")
        )

        result = await lastfm_operations.get_track_info_intelligent(sample_track)

        assert result is not None
        assert result.lastfm_title is None

    async def test_not_found_error_tries_next_artist(
        self, lastfm_operations, multi_artist_track
    ):
        """Track-not-found doesn't stop iteration, tries next artist."""
        call_count = 0

        async def mock_get_track_info_func(artist: str, title: str):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return None
            return LastFMTrackInfo(
                lastfm_title="Collaboration",
                lastfm_artist_name=artist,
                lastfm_url="https://last.fm/music/test",
            )

        mock_get_track_info = AsyncMock(side_effect=mock_get_track_info_func)
        lastfm_operations.client.get_track_info_comprehensive = mock_get_track_info

        result = await lastfm_operations.get_track_info_intelligent(multi_artist_track)

        assert result is not None
        assert result.lastfm_title == "Collaboration"
        assert mock_get_track_info.call_count == 2
