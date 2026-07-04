"""Tests for Last.fm connector ID consistency.

Validates that the inward resolver mints the same connector ID format
(the normalized ``artist::title`` composite, ``make_lastfm_identifier``) as
conversions.py and matching_provider.py, preventing duplicate canonical
tracks during history import. FLIPPED (v0.8.18 FM4a): Last.fm URLs are no
longer a connector_track_identifier scheme — every mint site now agrees on
the normalized composite, minted from Last.fm-CORRECTED names when available.
"""

from unittest.mock import AsyncMock, MagicMock

from src.domain.entities import Track
from src.domain.matching.protocols import Nothing
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


class TestInwardResolverUsesCompositeFormat:
    """The resolver mints the normalized artist::title composite — never a URL."""

    async def test_new_track_ignores_url_uses_composite(self):
        """Even when track.getInfo returns a URL, the connector mapping uses
        the normalized composite (from the corrected names), not the URL."""
        lastfm_client = AsyncMock()
        lastfm_url = "https://www.last.fm/music/Radiohead/_/Creep"
        lastfm_client.get_track_info_comprehensive.return_value = MagicMock(
            lastfm_url=lastfm_url,
            lastfm_duration=238000,
            lastfm_album_name="Pablo Honey",
            lastfm_mbid=None,
            lastfm_artist_name="Radiohead",
            lastfm_title="Creep",
        )

        cross_discovery = AsyncMock()
        cross_discovery.discover.return_value = Nothing()

        resolver = LastfmInwardResolver(
            lastfm_client=lastfm_client,
            cross_discovery=cross_discovery,
        )

        saved_track = make_track(id=42)
        uow = _make_uow(saved_track=saved_track)

        result, metrics = await resolver.resolve_to_canonical_tracks(
            ["radiohead::creep"], uow, user_id="test-user"
        )

        # The connector mapping uses the composite, never the URL.
        map_calls = uow.get_connector_repository().map_track_to_connector.call_args_list
        lastfm_calls = [c for c in map_calls if c.args[1] == "lastfm"]
        connector_ids = [c.args[2] for c in lastfm_calls]
        assert connector_ids == [make_lastfm_identifier("Radiohead", "Creep")]
        assert lastfm_url not in connector_ids

    async def test_getinfo_succeeds_without_names_falls_back_to_raw(self):
        """getInfo succeeding but omitting corrected names degrades to the raw
        pair inline — track.getCorrection is a fallback for getInfo FAILING,
        not for a successful-but-nameless response, so it's never called."""
        lastfm_client = AsyncMock()
        lastfm_client.get_track_info_comprehensive.return_value = MagicMock(
            lastfm_url=None,
            lastfm_duration=None,
            lastfm_album_name=None,
            lastfm_mbid=None,
            lastfm_artist_name=None,
            lastfm_title=None,
        )

        cross_discovery = AsyncMock()
        cross_discovery.discover.return_value = Nothing()

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
        assert connector_ids == [make_lastfm_identifier("radiohead", "creep")]
        lastfm_client.get_track_correction.assert_not_called()

    async def test_getinfo_fails_and_no_correction_falls_back_to_raw_names(self):
        """When track.getInfo fails outright, getCorrection is tried; when
        that ALSO has nothing on file, mint from the raw normalized names."""
        lastfm_client = AsyncMock()
        lastfm_client.get_track_info_comprehensive.return_value = None
        lastfm_client.get_track_correction.return_value = None

        cross_discovery = AsyncMock()
        cross_discovery.discover.return_value = Nothing()

        resolver = LastfmInwardResolver(
            lastfm_client=lastfm_client,
            cross_discovery=cross_discovery,
        )

        saved_track = make_track(id=42)
        uow = _make_uow(saved_track=saved_track)

        result, metrics = await resolver.resolve_to_canonical_tracks(
            ["radiohead::creep"], uow, user_id="test-user"
        )

        lastfm_client.get_track_correction.assert_called_once_with("radiohead", "creep")
        map_calls = uow.get_connector_repository().map_track_to_connector.call_args_list
        lastfm_calls = [c for c in map_calls if c.args[1] == "lastfm"]
        connector_ids = [c.args[2] for c in lastfm_calls]
        assert connector_ids == [make_lastfm_identifier("radiohead", "creep")]

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
