"""Tests for Spotify utility functions.

Validates create_track_from_spotify_data correctly converts SpotifyTrack Pydantic
models into domain Track entities with proper field mapping and validation.
Also tests the shared search_and_evaluate_match pipeline.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config import create_matching_config
from src.domain.entities import Artist
from src.domain.matching.evaluation_service import TrackMatchEvaluationService
from src.infrastructure.connectors.spotify.models import (
    SpotifyAlbum,
    SpotifyArtist,
    SpotifyExternalIds,
    SpotifyTrack,
)
from src.infrastructure.connectors.spotify.utilities import (
    SpotifySearchMatch,
    create_track_from_spotify_data,
    search_and_evaluate_match,
)
from tests.fixtures import make_track


class TestCreateTrackFromSpotifyData:
    """Happy path: valid SpotifyTrack produces correct domain Track."""

    def test_basic_track_conversion(self):
        spotify_track = SpotifyTrack(
            id="abc123",
            name="Test Song",
            artists=[SpotifyArtist(name="Test Artist")],
            album=SpotifyAlbum(name="Test Album"),
            duration_ms=240000,
            external_ids=SpotifyExternalIds(isrc="USRC12345678"),
        )

        track = create_track_from_spotify_data("abc123", spotify_track)

        assert track.title == "Test Song"
        assert track.artists == [Artist(name="Test Artist")]
        assert track.album == "Test Album"
        assert track.duration_ms == 240000
        assert track.isrc == "USRC12345678"
        assert track.connector_track_identifiers.get("spotify") == "abc123"

    def test_multiple_artists(self):
        spotify_track = SpotifyTrack(
            id="abc123",
            name="Collab Song",
            artists=[
                SpotifyArtist(name="Artist A"),
                SpotifyArtist(name="Artist B"),
            ],
        )

        track = create_track_from_spotify_data("abc123", spotify_track)

        assert len(track.artists) == 2
        assert track.artists[0].name == "Artist A"
        assert track.artists[1].name == "Artist B"

    def test_no_album(self):
        spotify_track = SpotifyTrack(
            id="abc123",
            name="Single",
            artists=[SpotifyArtist(name="Artist")],
            album=None,
        )

        track = create_track_from_spotify_data("abc123", spotify_track)

        assert track.album is None

    def test_zero_duration_treated_as_none(self):
        spotify_track = SpotifyTrack(
            id="abc123",
            name="Song",
            artists=[SpotifyArtist(name="Artist")],
            duration_ms=0,
        )

        track = create_track_from_spotify_data("abc123", spotify_track)

        assert track.duration_ms is None

    def test_no_isrc(self):
        spotify_track = SpotifyTrack(
            id="abc123",
            name="Song",
            artists=[SpotifyArtist(name="Artist")],
        )

        track = create_track_from_spotify_data("abc123", spotify_track)

        assert track.isrc is None


class TestCreateTrackFromSpotifyDataValidation:
    """Error cases: missing or invalid data raises ValueError."""

    def test_empty_name_raises(self):
        spotify_track = SpotifyTrack(
            id="abc123",
            name="",
            artists=[SpotifyArtist(name="Artist")],
        )

        with pytest.raises(ValueError, match="Missing track title"):
            create_track_from_spotify_data("abc123", spotify_track)

    def test_no_artists_raises(self):
        spotify_track = SpotifyTrack(
            id="abc123",
            name="Song",
            artists=[],
        )

        with pytest.raises(ValueError, match="Missing artists"):
            create_track_from_spotify_data("abc123", spotify_track)

    def test_artists_with_empty_names_raises(self):
        spotify_track = SpotifyTrack(
            id="abc123",
            name="Song",
            artists=[SpotifyArtist(name=""), SpotifyArtist(name="")],
        )

        with pytest.raises(ValueError, match="No valid artist names"):
            create_track_from_spotify_data("abc123", spotify_track)

    def test_skips_empty_artist_names(self):
        spotify_track = SpotifyTrack(
            id="abc123",
            name="Song",
            artists=[SpotifyArtist(name=""), SpotifyArtist(name="Valid")],
        )

        track = create_track_from_spotify_data("abc123", spotify_track)

        assert len(track.artists) == 1
        assert track.artists[0].name == "Valid"


# --- search_and_evaluate_match tests ---


def _make_candidate(
    *,
    track_id: str | None = "sp123",
    name: str = "Creep",
    artist: str = "Radiohead",
    duration_ms: int = 238000,
) -> MagicMock:
    artist_mock = MagicMock()
    artist_mock.name = artist

    candidate = MagicMock()
    candidate.id = track_id
    candidate.name = name
    candidate.artists = [artist_mock]
    candidate.duration_ms = duration_ms
    return candidate


def _make_connector(candidates: list[MagicMock] | None = None) -> AsyncMock:
    connector = AsyncMock()
    connector.search_track.return_value = candidates or []
    connector.connector_name = "spotify"
    return connector


@pytest.fixture
def evaluation_service() -> TrackMatchEvaluationService:
    return TrackMatchEvaluationService(config=create_matching_config())


class TestSearchAndEvaluateHappyPath:
    """Successful search should return SpotifySearchMatch with correct fields."""

    async def test_returns_match_with_correct_fields(
        self, evaluation_service: TrackMatchEvaluationService
    ):
        candidate = _make_candidate()
        connector = _make_connector([candidate])
        track = make_track(id=1, title="Creep", artist="Radiohead")

        result = await search_and_evaluate_match(
            connector, evaluation_service, track, "Radiohead", "Creep"
        )

        assert isinstance(result, SpotifySearchMatch)
        assert result.candidate is candidate
        assert result.similarity > 0.0
        assert result.match_result.confidence > 0
        connector.search_track.assert_called_once_with("Radiohead", "Creep")


class TestSearchAndEvaluateNoCandidates:
    """Empty search results should return None."""

    async def test_returns_none_when_no_candidates(
        self, evaluation_service: TrackMatchEvaluationService
    ):
        connector = _make_connector([])
        track = make_track(id=1)

        result = await search_and_evaluate_match(
            connector, evaluation_service, track, "Unknown", "Song"
        )

        assert result is None


class TestSearchAndEvaluateBelowThreshold:
    """Candidates below min_similarity should be rejected."""

    async def test_returns_none_below_threshold(
        self, evaluation_service: TrackMatchEvaluationService
    ):
        candidate = _make_candidate(name="Completely Different Title")
        connector = _make_connector([candidate])
        track = make_track(id=1, title="Creep", artist="Radiohead")

        result = await search_and_evaluate_match(
            connector,
            evaluation_service,
            track,
            "Radiohead",
            "Creep",
            min_similarity=0.99,
        )

        assert result is None


class TestSearchAndEvaluateNoIdWithoutFallback:
    """Candidate with no .id and no fallback_connector_id should return None."""

    async def test_returns_none_when_no_id(
        self, evaluation_service: TrackMatchEvaluationService
    ):
        candidate = _make_candidate(track_id=None)
        connector = _make_connector([candidate])
        track = make_track(id=1, title="Creep", artist="Radiohead")

        result = await search_and_evaluate_match(
            connector, evaluation_service, track, "Radiohead", "Creep"
        )

        assert result is None


class TestSearchAndEvaluateNoIdWithFallback:
    """Candidate with no .id but a fallback_connector_id should use the fallback."""

    async def test_uses_fallback_connector_id(
        self, evaluation_service: TrackMatchEvaluationService
    ):
        candidate = _make_candidate(track_id=None)
        connector = _make_connector([candidate])
        track = make_track(id=1, title="Creep", artist="Radiohead")

        result = await search_and_evaluate_match(
            connector,
            evaluation_service,
            track,
            "Radiohead",
            "Creep",
            fallback_connector_id="dead_id_123",
        )

        assert result is not None
        assert result.candidate is candidate
        assert result.match_result.connector_id == "dead_id_123"


class TestSearchAndEvaluateExceptionPropagation:
    """Exceptions from the connector should bubble up (not be caught)."""

    async def test_propagates_connector_exception(
        self, evaluation_service: TrackMatchEvaluationService
    ):
        connector = _make_connector()
        connector.search_track.side_effect = RuntimeError("API down")
        track = make_track(id=1)

        with pytest.raises(RuntimeError, match="API down"):
            await search_and_evaluate_match(
                connector, evaluation_service, track, "Radiohead", "Creep"
            )
