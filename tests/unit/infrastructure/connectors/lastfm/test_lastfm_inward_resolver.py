"""Tests for LastfmInwardResolver.

Validates that the Last.fm-specific inward resolver correctly creates skeletal
tracks, enriches via track.getInfo, uses URL-based connector IDs, and attempts
cross-service discovery via the CrossDiscoveryProvider protocol.
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

        cross_discovery = AsyncMock()
        cross_discovery.attempt_discovery.return_value = False

        saved_track = make_track(id=42)
        resolver = LastfmInwardResolver(
            lastfm_client=lastfm_client,
            cross_discovery=cross_discovery,
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


class TestCrossDiscovery:
    """Cross-discovery provider should be called for each new track."""

    async def test_successful_discovery_calls_provider(self):
        lastfm_client = AsyncMock()
        lastfm_client.get_track_info_comprehensive.return_value = MagicMock(
            lastfm_url="https://www.last.fm/music/Radiohead/_/Creep",
            lastfm_duration=238000,
            lastfm_album_name="Pablo Honey",
        )

        cross_discovery = AsyncMock()
        cross_discovery.attempt_discovery.return_value = True

        saved_track = make_track(id=42)
        resolver = LastfmInwardResolver(
            lastfm_client=lastfm_client,
            cross_discovery=cross_discovery,
        )

        uow = _make_uow(saved_track=saved_track)
        result, metrics = await resolver.resolve_to_canonical_tracks(
            ["radiohead::creep"], uow
        )

        # Should have called attempt_discovery
        cross_discovery.attempt_discovery.assert_called_once()
        call_args = cross_discovery.attempt_discovery.call_args
        assert call_args.args[1] == "radiohead"  # artist_name
        assert call_args.args[2] == "creep"  # track_name

    async def test_no_discovery_when_provider_is_none(self):
        lastfm_client = AsyncMock()
        lastfm_client.get_track_info_comprehensive.return_value = MagicMock(
            lastfm_url="https://www.last.fm/music/Radiohead/_/Creep",
            lastfm_duration=238000,
            lastfm_album_name="Pablo Honey",
        )

        saved_track = make_track(id=42)
        resolver = LastfmInwardResolver(
            lastfm_client=lastfm_client,
            cross_discovery=None,
        )

        uow = _make_uow(saved_track=saved_track)
        result, metrics = await resolver.resolve_to_canonical_tracks(
            ["radiohead::creep"], uow
        )

        # Track should still be created
        assert "radiohead::creep" in result
        assert metrics.created == 1


class TestDiscoveryRejected:
    """When cross-discovery returns False, track is still created."""

    async def test_failed_discovery_still_creates_track(self):
        lastfm_client = AsyncMock()
        lastfm_client.get_track_info_comprehensive.return_value = MagicMock(
            lastfm_url="https://www.last.fm/music/Radiohead/_/Creep",
            lastfm_duration=238000,
            lastfm_album_name=None,
        )

        cross_discovery = AsyncMock()
        cross_discovery.attempt_discovery.return_value = False

        saved_track = make_track(id=42)
        resolver = LastfmInwardResolver(
            lastfm_client=lastfm_client,
            cross_discovery=cross_discovery,
        )

        uow = _make_uow(saved_track=saved_track)
        result, metrics = await resolver.resolve_to_canonical_tracks(
            ["radiohead::creep"], uow
        )

        # Track should still be created
        assert "radiohead::creep" in result


class TestTrackInfoFailure:
    """When track.getInfo fails, fallback artist::title connector ID is used."""

    async def test_enrichment_failure_uses_fallback_id(self):
        lastfm_client = AsyncMock()
        lastfm_client.get_track_info_comprehensive.return_value = None  # Failure

        cross_discovery = AsyncMock()
        cross_discovery.attempt_discovery.return_value = False

        saved_track = make_track(id=42)
        resolver = LastfmInwardResolver(
            lastfm_client=lastfm_client,
            cross_discovery=cross_discovery,
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

        resolver = LastfmInwardResolver(
            lastfm_client=lastfm_client,
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
