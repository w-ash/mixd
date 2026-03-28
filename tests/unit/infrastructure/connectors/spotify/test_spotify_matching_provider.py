"""Tests for SpotifyProvider matching provider.

Tests ISRC matching, artist/title matching, raw match creation,
and ISRC-to-artist/title fallback behavior.
"""

from unittest.mock import AsyncMock, MagicMock

from src.domain.matching.types import MatchFailureReason
from src.infrastructure.connectors.spotify.matching_provider import SpotifyProvider
from src.infrastructure.connectors.spotify.models import (
    SpotifyAlbum,
    SpotifyArtist,
    SpotifyExternalIds,
    SpotifyTrack,
)
from tests.fixtures.factories import make_track


def _make_spotify_track_model(
    track_id: str = "sp_123",
    name: str = "Test Song",
    artist_name: str = "Test Artist",
    duration_ms: int = 240_000,
    isrc: str | None = None,
) -> MagicMock:
    """Create a mock SpotifyTrack Pydantic model."""
    mock = MagicMock()
    mock.id = track_id
    mock.name = name
    return mock


def _make_provider() -> tuple[SpotifyProvider, AsyncMock]:
    """Create a SpotifyProvider with a mocked connector."""
    connector = AsyncMock()
    provider = SpotifyProvider(connector_instance=connector)
    return provider, connector


class TestSpotifyProviderMatchByISRC:
    """Test ISRC-based matching via Spotify API."""

    async def test_successful_isrc_search_returns_match(self):
        """Successful ISRC search should produce a match."""
        provider, connector = _make_provider()
        track = make_track(isrc="USRC11111111")
        spotify_result = _make_spotify_track_model(
            track_id="sp_abc", isrc="USRC11111111"
        )
        connector.search_by_isrc.return_value = spotify_result

        matches, failures = await provider._match_by_isrc([track])

        assert track.id in matches
        assert matches[track.id]["connector_id"] == "sp_abc"
        assert matches[track.id]["match_method"] == "isrc"
        assert len(failures) == 0
        connector.search_by_isrc.assert_called_once_with("USRC11111111")

    async def test_no_results_returns_failure(self):
        """No Spotify results for ISRC should produce NO_RESULTS failure."""
        provider, connector = _make_provider()
        track = make_track(isrc="USRC00000000")
        connector.search_by_isrc.return_value = None

        matches, failures = await provider._match_by_isrc([track])

        assert len(matches) == 0
        assert len(failures) == 1
        assert failures[0].reason == MatchFailureReason.NO_RESULTS
        assert failures[0].track_id == track.id

    async def test_api_error_returns_failure(self):
        """API exception during ISRC search should produce API_ERROR failure."""
        provider, connector = _make_provider()
        track = make_track(isrc="USRC11111111")
        connector.search_by_isrc.side_effect = RuntimeError("Connection timeout")

        matches, failures = await provider._match_by_isrc([track])

        assert len(matches) == 0
        assert len(failures) == 1
        assert failures[0].reason == MatchFailureReason.API_ERROR
        assert failures[0].exception_type == "RuntimeError"

    async def test_track_without_isrc_returns_no_isrc_failure(self):
        """Track missing ISRC should produce NO_ISRC failure."""
        provider, connector = _make_provider()
        track = make_track(isrc=None)

        matches, failures = await provider._match_by_isrc([track])

        assert len(matches) == 0
        assert len(failures) == 1
        assert failures[0].reason == MatchFailureReason.NO_ISRC


class TestSpotifyProviderMatchByArtistTitle:
    """Test artist/title-based matching via Spotify API."""

    async def test_successful_search_picks_best_candidate(self):
        """Should pick the candidate with highest title similarity."""
        provider, connector = _make_provider()
        track = make_track(title="Karma Police", artist="Radiohead")

        # Return two candidates — exact match and a partial match
        exact = _make_spotify_track_model(
            track_id="sp_exact", name="Karma Police", artist_name="Radiohead"
        )
        partial = _make_spotify_track_model(
            track_id="sp_partial", name="Karma Police - Live", artist_name="Radiohead"
        )
        connector.search_track.return_value = [partial, exact]

        matches, failures = await provider._match_by_artist_title([track])

        assert track.id in matches
        assert matches[track.id]["connector_id"] == "sp_exact"
        assert matches[track.id]["match_method"] == "artist_title"

    async def test_no_results_returns_failure(self):
        """Empty search results should produce NO_RESULTS failure."""
        provider, connector = _make_provider()
        track = make_track(title="Obscure Song", artist="Unknown Artist")
        connector.search_track.return_value = []

        matches, failures = await provider._match_by_artist_title([track])

        assert len(matches) == 0
        assert len(failures) == 1
        assert failures[0].reason == MatchFailureReason.NO_RESULTS

    async def test_api_error_handled_gracefully(self):
        """API exception during search should produce API_ERROR failure."""
        provider, connector = _make_provider()
        track = make_track()
        connector.search_track.side_effect = RuntimeError("Rate limited")

        matches, failures = await provider._match_by_artist_title([track])

        assert len(matches) == 0
        assert len(failures) == 1
        assert failures[0].reason == MatchFailureReason.API_ERROR

    async def test_track_without_metadata_returns_failure(self):
        """Track without artist or title should produce NO_METADATA failure."""
        provider, connector = _make_provider()
        track = make_track(title="", artist="Artist")

        matches, failures = await provider._match_by_artist_title([track])

        assert len(matches) == 0
        assert len(failures) == 1
        assert failures[0].reason == MatchFailureReason.NO_METADATA


class TestSpotifyProviderCreateRawMatch:
    """Test raw match creation from Spotify API response data."""

    def test_extracts_correct_fields(self):
        """Should extract all relevant fields from SpotifyTrack model."""
        provider, _ = _make_provider()
        spotify_track = SpotifyTrack(
            id="sp_123",
            name="Test Song",
            artists=[SpotifyArtist(name="Artist 1"), SpotifyArtist(name="Artist 2")],
            duration_ms=240_000,
            album=SpotifyAlbum(name="Test Album", release_date="2024-01-01"),
            external_ids=SpotifyExternalIds(isrc="USRC11111111"),
        )

        result = provider._create_raw_match(spotify_track, "isrc")

        assert result is not None
        assert result["connector_id"] == "sp_123"
        assert result["match_method"] == "isrc"
        assert result["service_data"]["title"] == "Test Song"
        assert result["service_data"]["artist"] == "Artist 1"
        assert result["service_data"]["artists"] == ["Artist 1", "Artist 2"]
        assert result["service_data"]["duration_ms"] == 240_000
        assert result["service_data"]["album"] == "Test Album"
        assert result["service_data"]["isrc"] == "USRC11111111"

    def test_handles_missing_optional_fields(self):
        """Should handle missing optional fields gracefully."""
        provider, _ = _make_provider()
        spotify_track = SpotifyTrack(
            id="sp_minimal",
            name="Minimal Track",
            artists=[],
            duration_ms=0,
        )

        result = provider._create_raw_match(spotify_track, "artist_title")

        assert result is not None
        assert result["connector_id"] == "sp_minimal"
        assert result["service_data"]["artist"] == ""
        assert result["service_data"]["artists"] == []
        assert result["service_data"]["duration_ms"] == 0

    def test_returns_none_on_exception(self):
        """Should return None if data extraction fails."""
        provider, _ = _make_provider()
        # Pass a MagicMock that will raise on attribute access
        bad_model = MagicMock(spec=[])
        bad_model.id = "sp_123"
        bad_model.name = "Test"
        # artists attribute raises when iterated
        bad_model.artists = MagicMock(side_effect=RuntimeError("broken"))

        result = provider._create_raw_match(bad_model, "isrc")

        assert result is None


class TestSpotifyProviderISRCFallback:
    """Test ISRC-to-artist/title fallback behavior (Step 1 fix)."""

    async def test_failed_isrc_tracks_fall_back_to_artist_title(self):
        """Tracks that fail ISRC matching should be retried via artist/title."""
        provider, connector = _make_provider()

        # Track has both ISRC and artist/title
        track = make_track(
            title="Paranoid Android", artist="Radiohead", isrc="USRC11111111"
        )

        # ISRC search returns nothing
        connector.search_by_isrc.return_value = None

        # Artist/title search succeeds
        fallback_result = _make_spotify_track_model(
            track_id="sp_fallback", name="Paranoid Android", artist_name="Radiohead"
        )
        connector.search_track.return_value = [fallback_result]

        result = await provider.fetch_raw_matches_for_tracks([track])

        assert len(result.matches) == 1
        assert track.id in result.matches
        assert result.matches[track.id]["connector_id"] == "sp_fallback"
        assert result.matches[track.id]["match_method"] == "artist_title"

    async def test_successful_isrc_tracks_not_retried(self):
        """Tracks that succeed via ISRC should NOT be sent to artist/title."""
        provider, connector = _make_provider()

        track = make_track(title="Song", artist="Artist", isrc="USRC11111111")

        # ISRC search succeeds
        spotify_result = _make_spotify_track_model(track_id="sp_isrc")
        connector.search_by_isrc.return_value = spotify_result

        result = await provider.fetch_raw_matches_for_tracks([track])

        assert len(result.matches) == 1
        assert result.matches[track.id]["match_method"] == "isrc"
        connector.search_track.assert_not_called()

    async def test_mixed_isrc_and_non_isrc_tracks(self):
        """Mixed batch: ISRC success + ISRC failure (fallback) + non-ISRC."""
        provider, connector = _make_provider()

        track_isrc_success = make_track(title="A", artist="X", isrc="ISRC_OK")
        track_isrc_fail = make_track(title="B", artist="Y", isrc="ISRC_FAIL")
        track_no_isrc = make_track(title="C", artist="Z")

        # ISRC: track 1 succeeds, track 2 fails
        async def isrc_side_effect(isrc: str):
            if isrc == "ISRC_OK":
                return _make_spotify_track_model(track_id="sp_1")
            return None

        connector.search_by_isrc.side_effect = isrc_side_effect

        # Artist/title: tracks 2 and 3 succeed
        async def search_side_effect(artist: str, title: str):
            return [_make_spotify_track_model(track_id=f"sp_{title}", name=title)]

        connector.search_track.side_effect = search_side_effect

        result = await provider.fetch_raw_matches_for_tracks([
            track_isrc_success,
            track_isrc_fail,
            track_no_isrc,
        ])

        assert len(result.matches) == 3
        assert result.matches[track_isrc_success.id]["match_method"] == "isrc"
        assert result.matches[track_isrc_fail.id]["match_method"] == "artist_title"
        assert result.matches[track_no_isrc.id]["match_method"] == "artist_title"
