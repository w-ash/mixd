"""Tests for Last.fm connector ID consistency.

Validates that the inward resolver uses the same connector ID format
(Last.fm URLs) as conversions.py and matching_provider.py, preventing duplicate
canonical tracks during history import.
"""

from unittest.mock import AsyncMock, MagicMock

from src.domain.entities import Track
from src.infrastructure.connectors.lastfm.identifiers import make_lastfm_identifier
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
    # Canonical Reuse: default to no title+artist matches
    track_repo.find_tracks_by_title_artist.return_value = {}

    connector_repo = uow.get_connector_repository()
    connector_repo.find_tracks_by_connectors.return_value = existing_tracks or {}
    connector_repo.map_track_to_connector.return_value = default_track

    return uow


class TestLastfmIdentifierNormalization:
    """Dedup identifiers used by the resolution service."""

    def test_identifier_is_lowercased_for_dedup(self):
        id1 = make_lastfm_identifier("Radiohead", "Creep")
        id2 = make_lastfm_identifier("radiohead", "creep")

        assert id1 == id2

    def test_identifier_strips_whitespace(self):
        id1 = make_lastfm_identifier("Radiohead", "Creep")
        id2 = make_lastfm_identifier("  Radiohead  ", "  Creep  ")

        assert id1 == id2


class TestInwardResolverUsesUrlFormat:
    """Integration-level test: the resolver should produce URL-based connector IDs."""

    async def test_new_track_uses_url_from_track_getinfo(self):
        """When track.getInfo returns a URL, the connector mapping should use it."""
        lastfm_client = AsyncMock()
        lastfm_url = "https://www.last.fm/music/Radiohead/_/Creep"
        lastfm_client.get_track_info_comprehensive.return_value = MagicMock(
            lastfm_url=lastfm_url,
            lastfm_duration=238000,
            lastfm_album_name="Pablo Honey",
        )

        cross_discovery = AsyncMock()
        cross_discovery.attempt_discovery.return_value = False

        resolver = LastfmInwardResolver(
            lastfm_client=lastfm_client,
            cross_discovery=cross_discovery,
        )

        saved_track = make_track(id=42)
        uow = _make_uow(saved_track=saved_track)

        result, metrics = await resolver.resolve_to_canonical_tracks(
            ["radiohead::creep"], uow, user_id="test-user"
        )

        # The connector mapping should use the URL
        map_calls = uow.get_connector_repository().map_track_to_connector.call_args_list
        lastfm_calls = [c for c in map_calls if c.args[1] == "lastfm"]
        connector_ids = [c.args[2] for c in lastfm_calls]
        assert lastfm_url in connector_ids

    async def test_new_track_falls_back_when_no_url(self):
        """When track.getInfo returns no URL, fall back to artist::title."""
        lastfm_client = AsyncMock()
        lastfm_client.get_track_info_comprehensive.return_value = MagicMock(
            lastfm_url=None,
            lastfm_duration=None,
            lastfm_album_name=None,
        )

        cross_discovery = AsyncMock()
        cross_discovery.attempt_discovery.return_value = False

        resolver = LastfmInwardResolver(
            lastfm_client=lastfm_client,
            cross_discovery=cross_discovery,
        )

        saved_track = make_track(id=42)
        uow = _make_uow(saved_track=saved_track)

        result, metrics = await resolver.resolve_to_canonical_tracks(
            ["radiohead::creep"], uow, user_id="test-user"
        )

        map_calls = uow.get_connector_repository().map_track_to_connector.call_args_list
        lastfm_calls = [c for c in map_calls if c.args[1] == "lastfm"]
        connector_ids = [c.args[2] for c in lastfm_calls]
        # Should use artist::title fallback
        assert any("::" in cid and "last.fm" not in cid for cid in connector_ids)

    async def test_bulk_lookup_finds_existing_url_tracks(self):
        """Mapping lookup should find tracks stored with artist::title dedup key."""
        resolver = LastfmInwardResolver(
            lastfm_client=AsyncMock(),
        )

        existing_track = make_track(id=10)
        uow = _make_uow(
            existing_tracks={("lastfm", "radiohead::creep"): existing_track}
        )

        result, metrics = await resolver.resolve_to_canonical_tracks(
            ["radiohead::creep"], uow, user_id="test-user"
        )

        # Should find the existing track via bulk lookup
        assert result["radiohead::creep"] == existing_track
        assert metrics.existing == 1
        assert metrics.created == 0

        # Verify find_tracks_by_connectors was called
        uow.get_connector_repository().find_tracks_by_connectors.assert_called_once()
