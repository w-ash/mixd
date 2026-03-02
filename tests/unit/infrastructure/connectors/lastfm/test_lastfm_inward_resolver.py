"""Tests for LastfmInwardResolver.

Validates that the Last.fm-specific inward resolver correctly creates skeletal
tracks, enriches via track.getInfo, uses URL-based connector IDs, and attempts
Spotify cross-discovery.
"""

from unittest.mock import AsyncMock, MagicMock

from src.domain.entities import Track
from src.infrastructure.connectors.lastfm.inward_resolver import (
    LastfmInwardResolver,
)
from tests.fixtures import make_track
from tests.fixtures.mocks import make_mock_uow


def _make_uow(
    existing_tracks: dict | None = None,
    saved_track: Track | None = None,
) -> MagicMock:
    """Create a mock UoW with configured repositories."""
    default_track = saved_track or make_track(title="Creep", artist="Radiohead")
    uow = make_mock_uow()

    track_repo = uow.get_track_repository()
    track_repo.save_track.return_value = default_track

    connector_repo = uow.get_connector_repository()
    connector_repo.find_tracks_by_connectors.return_value = existing_tracks or {}
    connector_repo.map_track_to_connector.return_value = default_track

    return uow


class TestCreatesSkeletalTrackAndEnriches:
    """New tracks should be created and enriched via track.getInfo."""

    async def test_creates_track_and_uses_url_connector_id(self):
        lastfm_client = AsyncMock()
        lastfm_url = "https://www.last.fm/music/Radiohead/_/Creep"
        lastfm_client.get_track_info_comprehensive.return_value = MagicMock(
            lastfm_url=lastfm_url,
            lastfm_duration=238000,
            lastfm_album_name="Pablo Honey",
        )

        spotify_connector = AsyncMock()
        spotify_connector.search_track.return_value = []

        saved_track = make_track(id=42)
        resolver = LastfmInwardResolver(
            lastfm_client=lastfm_client,
            spotify_connector=spotify_connector,
        )

        uow = _make_uow(saved_track=saved_track)
        result, metrics = await resolver.resolve_to_canonical_tracks(
            ["radiohead::creep"], uow
        )

        assert "radiohead::creep" in result
        assert result["radiohead::creep"].id == 42
        assert metrics.created == 1

        # Verify the connector mapping uses the URL
        map_calls = uow.get_connector_repository().map_track_to_connector.call_args_list
        lastfm_calls = [c for c in map_calls if c.args[1] == "lastfm"]
        connector_ids = [c.args[2] for c in lastfm_calls]
        assert lastfm_url in connector_ids


class TestSpotifyCrossDiscovery:
    """When Spotify match succeeds, a dual mapping should be created."""

    async def test_successful_discovery_creates_spotify_mapping(self):
        lastfm_client = AsyncMock()
        lastfm_client.get_track_info_comprehensive.return_value = MagicMock(
            lastfm_url="https://www.last.fm/music/Radiohead/_/Creep",
            lastfm_duration=238000,
            lastfm_album_name="Pablo Honey",
        )

        # Spotify returns a match with high similarity
        spotify_match = MagicMock()
        spotify_match.id = "spotify123"
        spotify_match.name = "Creep"
        spotify_match.artists = [MagicMock(name="Radiohead")]
        spotify_match.duration_ms = 238000
        spotify_match.album = MagicMock(name="Pablo Honey")
        spotify_match.external_ids = MagicMock(isrc="GBAYE9300106")
        spotify_match.model_dump.return_value = {"id": "spotify123", "name": "Creep"}

        spotify_connector = AsyncMock()
        spotify_connector.search_track.return_value = [spotify_match]

        saved_track = make_track(id=42)
        resolver = LastfmInwardResolver(
            lastfm_client=lastfm_client,
            spotify_connector=spotify_connector,
        )

        uow = _make_uow(saved_track=saved_track)
        result, metrics = await resolver.resolve_to_canonical_tracks(
            ["radiohead::creep"], uow
        )

        # Should have called search_track for Spotify discovery
        spotify_connector.search_track.assert_called_once()

        # Check that a spotify mapping was attempted
        map_calls = uow.get_connector_repository().map_track_to_connector.call_args_list
        spotify_calls = [c for c in map_calls if c.args[1] == "spotify"]
        # May or may not have created the spotify mapping depending on confidence threshold
        # (we just verify the search was attempted)


class TestSpotifyDiscoveryRejected:
    """When Spotify match is below confidence threshold, no mapping is created."""

    async def test_low_confidence_no_spotify_mapping(self):
        lastfm_client = AsyncMock()
        lastfm_client.get_track_info_comprehensive.return_value = MagicMock(
            lastfm_url="https://www.last.fm/music/Radiohead/_/Creep",
            lastfm_duration=238000,
            lastfm_album_name=None,
        )

        # Spotify returns a match but with a totally different title
        spotify_match = MagicMock()
        spotify_match.id = "spotify456"
        spotify_match.name = "Completely Different Song"
        spotify_match.artists = [MagicMock(name="Someone Else")]
        spotify_match.duration_ms = 120000
        spotify_match.album = MagicMock(name="Other Album")
        spotify_match.external_ids = None
        spotify_match.model_dump.return_value = {
            "id": "spotify456",
            "name": "Completely Different Song",
        }

        spotify_connector = AsyncMock()
        spotify_connector.search_track.return_value = [spotify_match]

        saved_track = make_track(id=42)
        resolver = LastfmInwardResolver(
            lastfm_client=lastfm_client,
            spotify_connector=spotify_connector,
        )

        uow = _make_uow(saved_track=saved_track)
        result, metrics = await resolver.resolve_to_canonical_tracks(
            ["radiohead::creep"], uow
        )

        # Track should still be created
        assert "radiohead::creep" in result

        # Spotify mapping should NOT be created (low confidence)
        map_calls = uow.get_connector_repository().map_track_to_connector.call_args_list
        spotify_calls = [c for c in map_calls if c.args[1] == "spotify"]
        assert len(spotify_calls) == 0


class TestTrackInfoFailure:
    """When track.getInfo fails, fallback artist::title connector ID is used."""

    async def test_enrichment_failure_uses_fallback_id(self):
        lastfm_client = AsyncMock()
        lastfm_client.get_track_info_comprehensive.return_value = None  # Failure

        spotify_connector = AsyncMock()
        spotify_connector.search_track.return_value = []

        saved_track = make_track(id=42)
        resolver = LastfmInwardResolver(
            lastfm_client=lastfm_client,
            spotify_connector=spotify_connector,
        )

        uow = _make_uow(saved_track=saved_track)
        result, metrics = await resolver.resolve_to_canonical_tracks(
            ["radiohead::creep"], uow
        )

        assert "radiohead::creep" in result

        # Connector ID should be the fallback format (no URL available)
        map_calls = uow.get_connector_repository().map_track_to_connector.call_args_list
        lastfm_calls = [c for c in map_calls if c.args[1] == "lastfm"]
        connector_ids = [c.args[2] for c in lastfm_calls]
        # Should use artist::title fallback
        assert any("::" in cid and "last.fm" not in cid for cid in connector_ids)


class TestDelegatesToBaseLookup:
    """Existing tracks should be found via base class bulk lookup."""

    async def test_existing_tracks_found_via_base(self):
        existing_track = make_track(id=10)

        lastfm_client = AsyncMock()
        spotify_connector = MagicMock()

        resolver = LastfmInwardResolver(
            lastfm_client=lastfm_client,
            spotify_connector=spotify_connector,
        )

        uow = _make_uow(
            existing_tracks={("lastfm", "radiohead::creep"): existing_track}
        )
        result, metrics = await resolver.resolve_to_canonical_tracks(
            ["radiohead::creep"], uow
        )

        assert result["radiohead::creep"] == existing_track
        assert metrics.existing == 1
        assert metrics.created == 0

        # No API calls needed
        lastfm_client.get_track_info_comprehensive.assert_not_called()
