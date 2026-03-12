"""Tests for SpotifyCrossDiscoveryProvider.

Validates that the extracted cross-discovery logic correctly searches Spotify,
evaluates match quality via the domain service, and creates connector mappings.
"""

from unittest.mock import AsyncMock, MagicMock

from src.infrastructure.connectors.spotify.cross_discovery import (
    SpotifyCrossDiscoveryProvider,
)
from tests.fixtures import make_track
from tests.fixtures.mocks import make_mock_uow


def _make_uow() -> MagicMock:
    uow = make_mock_uow()
    connector_repo = uow.get_connector_repository()
    connector_repo.map_track_to_connector.return_value = make_track(1)
    track_repo = uow.get_track_repository()
    track_repo.save_track.return_value = make_track(1)
    return uow


class TestSuccessfulDiscovery:
    """High-confidence matches should create a Spotify connector mapping."""

    async def test_creates_mapping_for_matching_track(self):
        artist_mock = MagicMock()
        artist_mock.name = "Radiohead"

        album_mock = MagicMock()
        album_mock.name = "Pablo Honey"

        spotify_match = MagicMock()
        spotify_match.id = "spotify123"
        spotify_match.name = "Creep"
        spotify_match.artists = [artist_mock]
        spotify_match.duration_ms = 238000
        spotify_match.album = album_mock
        spotify_match.external_ids = MagicMock(isrc="GBAYE9300106")
        spotify_match.model_dump.return_value = {"id": "spotify123", "name": "Creep"}

        connector = AsyncMock()
        connector.search_track.return_value = [spotify_match]

        provider = SpotifyCrossDiscoveryProvider(spotify_connector=connector)
        track = make_track(id=42, title="Creep", artist="Radiohead")
        uow = _make_uow()

        result = await provider.attempt_discovery(track, "Radiohead", "Creep", uow)

        assert result is True
        connector.search_track.assert_called_once_with("Radiohead", "Creep")
        uow.get_connector_repository().map_track_to_connector.assert_called()


class TestNoResults:
    """Empty search results should return False without creating mappings."""

    async def test_returns_false_when_no_candidates(self):
        connector = AsyncMock()
        connector.search_track.return_value = []

        provider = SpotifyCrossDiscoveryProvider(spotify_connector=connector)
        track = make_track(id=42)
        uow = _make_uow()

        result = await provider.attempt_discovery(track, "Unknown", "Song", uow)

        assert result is False
        uow.get_connector_repository().map_track_to_connector.assert_not_called()


class TestLowConfidence:
    """Poor matches should be rejected by the domain evaluation service."""

    async def test_rejects_dissimilar_track(self):
        spotify_match = MagicMock()
        spotify_match.id = "spotify456"
        spotify_match.name = "Completely Different Song"
        spotify_match.artists = [MagicMock(name="Someone Else")]
        spotify_match.duration_ms = 120000
        spotify_match.album = None
        spotify_match.external_ids = None
        spotify_match.model_dump.return_value = {
            "id": "spotify456",
            "name": "Completely Different Song",
        }

        connector = AsyncMock()
        connector.search_track.return_value = [spotify_match]

        provider = SpotifyCrossDiscoveryProvider(spotify_connector=connector)
        track = make_track(id=42, title="Creep", artist="Radiohead")
        uow = _make_uow()

        result = await provider.attempt_discovery(track, "Radiohead", "Creep", uow)

        assert result is False
        uow.get_connector_repository().map_track_to_connector.assert_not_called()


class TestExceptionHandling:
    """API errors should be caught and return False."""

    async def test_returns_false_on_search_error(self):
        connector = AsyncMock()
        connector.search_track.side_effect = RuntimeError("API down")

        provider = SpotifyCrossDiscoveryProvider(spotify_connector=connector)
        track = make_track(id=42)
        uow = _make_uow()

        result = await provider.attempt_discovery(track, "Radiohead", "Creep", uow)

        assert result is False
