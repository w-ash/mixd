"""Tests for LastFMProvider matching provider.

Tests batch lookup, raw match creation, failure classification,
and the fact that LastFM does NOT use _match_by_isrc/_match_by_artist_title.
"""

from unittest.mock import AsyncMock

from src.domain.matching.types import MatchFailureReason
from src.infrastructure.connectors.lastfm.conversions import LastFMTrackInfo
from src.infrastructure.connectors.lastfm.matching_provider import LastFMProvider
from tests.fixtures.factories import make_track


def _make_lastfm_track_info(
    title: str = "Test Song",
    artist: str = "Test Artist",
    url: str = "https://last.fm/track/123",
    mbid: str | None = None,
    duration: int | None = 240_000,
    user_playcount: int = 10,
    global_playcount: int = 50_000,
    listeners: int = 5_000,
    user_loved: bool = False,
) -> LastFMTrackInfo:
    """Create a LastFMTrackInfo for testing."""
    return LastFMTrackInfo(
        lastfm_title=title,
        lastfm_artist_name=artist,
        lastfm_url=url,
        lastfm_duration=duration,
        lastfm_user_playcount=user_playcount,
        lastfm_global_playcount=global_playcount,
        lastfm_listeners=listeners,
        lastfm_user_loved=user_loved,
        lastfm_mbid=mbid,
    )


def _make_provider() -> tuple[LastFMProvider, AsyncMock]:
    """Create a LastFMProvider with a mocked connector."""
    connector = AsyncMock()
    provider = LastFMProvider(connector_instance=connector)
    return provider, connector


class TestLastFMProviderFetchRawMatches:
    """Test batch matching via LastFM API."""

    async def test_successful_batch_lookup_returns_matches(self):
        """Successful batch lookup should produce matches for each track."""
        provider, connector = _make_provider()
        tracks = [
            make_track(id=1, title="Song A", artist="Artist A"),
            make_track(id=2, title="Song B", artist="Artist B"),
        ]

        connector.get_track_info_batch.return_value = {
            1: _make_lastfm_track_info(title="Song A", artist="Artist A"),
            2: _make_lastfm_track_info(title="Song B", artist="Artist B"),
        }

        result = await provider.fetch_raw_matches_for_tracks(tracks)

        assert len(result.matches) == 2
        assert 1 in result.matches
        assert 2 in result.matches
        assert len(result.failures) == 0

    async def test_missing_tracks_get_no_results_failure(self):
        """Tracks absent from API response should get NO_RESULTS failure."""
        provider, connector = _make_provider()
        tracks = [make_track(id=1), make_track(id=2)]

        # Only track 1 returned
        connector.get_track_info_batch.return_value = {
            1: _make_lastfm_track_info(),
        }

        result = await provider.fetch_raw_matches_for_tracks(tracks)

        assert len(result.matches) == 1
        assert 1 in result.matches
        failures_for_2 = [f for f in result.failures if f.track_id == 2]
        assert len(failures_for_2) == 1
        assert failures_for_2[0].reason == MatchFailureReason.NO_RESULTS

    async def test_empty_tracks_returns_empty_result(self):
        """Empty track list should return empty result without API call."""
        provider, connector = _make_provider()

        result = await provider.fetch_raw_matches_for_tracks([])

        assert len(result.matches) == 0
        assert len(result.failures) == 0
        connector.get_track_info_batch.assert_not_called()

    async def test_batch_api_failure_records_failure_for_all_tracks(self):
        """Batch API exception should produce failures for all tracks."""
        provider, connector = _make_provider()
        tracks = [make_track(id=1), make_track(id=2), make_track(id=3)]
        connector.get_track_info_batch.side_effect = RuntimeError("API down")

        result = await provider.fetch_raw_matches_for_tracks(tracks)

        assert len(result.matches) == 0
        assert len(result.failures) == 3
        assert all(f.reason == MatchFailureReason.API_ERROR for f in result.failures)
        track_ids = {f.track_id for f in result.failures}
        assert track_ids == {1, 2, 3}

    async def test_track_without_lastfm_url_creates_failure(self):
        """Track info without lastfm_url should produce NO_RESULTS failure."""
        provider, connector = _make_provider()
        tracks = [make_track(id=1)]

        connector.get_track_info_batch.return_value = {
            1: LastFMTrackInfo(
                lastfm_title="Song", lastfm_artist_name="Artist", lastfm_url=None
            ),
        }

        result = await provider.fetch_raw_matches_for_tracks(tracks)

        assert len(result.matches) == 0
        assert len(result.failures) == 1
        assert result.failures[0].reason == MatchFailureReason.NO_RESULTS

    async def test_track_with_lastfm_url_creates_valid_match(self):
        """Track info with lastfm_url should produce a valid match."""
        provider, connector = _make_provider()
        tracks = [make_track(id=1)]

        connector.get_track_info_batch.return_value = {
            1: _make_lastfm_track_info(url="https://last.fm/music/Artist/_/Song"),
        }

        result = await provider.fetch_raw_matches_for_tracks(tracks)

        assert len(result.matches) == 1
        assert (
            result.matches[1]["connector_id"] == "https://last.fm/music/Artist/_/Song"
        )


class TestLastFMProviderCreateRawMatch:
    """Test raw match creation from LastFM track info."""

    def test_extracts_lastfm_specific_fields(self):
        """Should extract LastFM-specific metadata into service_data."""
        provider, _ = _make_provider()
        track_info = _make_lastfm_track_info(
            title="Creep",
            artist="Radiohead",
            user_playcount=42,
            global_playcount=100_000,
            listeners=25_000,
            user_loved=True,
        )

        result = provider._create_raw_match(track_info)

        assert result is not None
        assert result["service_data"]["title"] == "Creep"
        assert result["service_data"]["artist"] == "Radiohead"
        assert result["service_data"]["lastfm_user_playcount"] == 42
        assert result["service_data"]["lastfm_global_playcount"] == 100_000
        assert result["service_data"]["lastfm_listeners"] == 25_000
        assert result["service_data"]["lastfm_user_loved"] is True

    def test_match_method_is_mbid_when_mbid_present(self):
        """Should classify match method as 'mbid' when MBID is available."""
        provider, _ = _make_provider()
        track_info = _make_lastfm_track_info(mbid="abc-123-def")

        result = provider._create_raw_match(track_info)

        assert result is not None
        assert result["match_method"] == "mbid"

    def test_match_method_is_artist_title_without_mbid(self):
        """Should classify match method as 'artist_title' when no MBID."""
        provider, _ = _make_provider()
        track_info = _make_lastfm_track_info(mbid=None)

        result = provider._create_raw_match(track_info)

        assert result is not None
        assert result["match_method"] == "artist_title"

    def test_returns_none_on_exception(self):
        """Should return None if data extraction fails."""
        provider, _ = _make_provider()

        result = provider._create_raw_match(None)  # type: ignore[arg-type]

        assert result is None


class TestLastFMProviderProtocolCompliance:
    """Verify that LastFMProvider satisfies the MatchProvider protocol."""

    def test_has_service_name(self):
        provider, _ = _make_provider()
        assert provider.service_name == "lastfm"

    def test_has_fetch_raw_matches_for_tracks(self):
        provider, _ = _make_provider()
        assert callable(provider.fetch_raw_matches_for_tracks)
