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
    # Canonical Reuse: default to no title+artist matches
    track_repo.find_tracks_by_title_artist.return_value = {}

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


class TestMBIDEnrichment:
    """Track enrichment should populate MBID from track.getInfo."""

    async def test_enrichment_sets_mbid_from_track_info(self):
        """When track.getInfo returns an MBID, it should be set on the enriched track."""
        lastfm_client = AsyncMock()
        test_mbid = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        lastfm_client.get_track_info_comprehensive.return_value = MagicMock(
            lastfm_url="https://www.last.fm/music/Radiohead/_/Creep",
            lastfm_duration=238000,
            lastfm_album_name="Pablo Honey",
            lastfm_mbid=test_mbid,
        )

        cross_discovery = AsyncMock()
        cross_discovery.attempt_discovery.return_value = False

        saved_track = make_track(id=42)
        resolver = LastfmInwardResolver(
            lastfm_client=lastfm_client,
            cross_discovery=cross_discovery,
        )

        uow = _make_uow(saved_track=saved_track)
        result, _ = await resolver.resolve_to_canonical_tracks(
            ["radiohead::creep"], uow
        )

        assert "radiohead::creep" in result

        # Verify save_track was called with connector_track_identifiers containing musicbrainz
        track_repo = uow.get_track_repository()
        save_calls = track_repo.save_track.call_args_list
        # The enrichment call should include the MBID in connector_track_identifiers
        enrichment_calls = [
            c
            for c in save_calls
            if c.args[0].connector_track_identifiers.get("musicbrainz") == test_mbid
        ]
        assert len(enrichment_calls) >= 1

    async def test_no_mbid_when_track_info_has_none(self):
        """When track.getInfo returns no MBID, connector_track_identifiers is unchanged."""
        lastfm_client = AsyncMock()
        lastfm_client.get_track_info_comprehensive.return_value = MagicMock(
            lastfm_url="https://www.last.fm/music/Radiohead/_/Creep",
            lastfm_duration=238000,
            lastfm_album_name="Pablo Honey",
            lastfm_mbid=None,
        )

        cross_discovery = AsyncMock()
        cross_discovery.attempt_discovery.return_value = False

        saved_track = make_track(id=42)
        resolver = LastfmInwardResolver(
            lastfm_client=lastfm_client,
            cross_discovery=cross_discovery,
        )

        uow = _make_uow(saved_track=saved_track)
        result, _ = await resolver.resolve_to_canonical_tracks(
            ["radiohead::creep"], uow
        )

        assert "radiohead::creep" in result
        # Verify that no save call includes "musicbrainz" in connector_track_identifiers
        track_repo = uow.get_track_repository()
        save_calls = track_repo.save_track.call_args_list
        for call in save_calls:
            track_arg = call.args[0]
            assert "musicbrainz" not in track_arg.connector_track_identifiers


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


class TestCanonicalReuse:
    """Canonical reuse finds existing canonical tracks by title+artist and creates mappings."""

    async def test_reuses_existing_track_by_title_artist(self):
        """When a canonical track exists with matching title+artist, reuse it."""
        existing_track = make_track(id=10, title="Creep", artist="Radiohead")

        lastfm_client = AsyncMock()

        resolver = LastfmInwardResolver(
            lastfm_client=lastfm_client,
        )

        uow = _make_uow()
        track_repo = uow.get_track_repository()
        connector_repo = uow.get_connector_repository()

        # Mapping Lookup: no connector mapping found
        connector_repo.find_tracks_by_connectors.return_value = {}
        # Canonical Reuse: title+artist lookup finds the existing track
        track_repo.find_tracks_by_title_artist.return_value = {
            ("creep", "radiohead"): existing_track,
        }

        result, metrics = await resolver.resolve_to_canonical_tracks(
            ["radiohead::creep"], uow
        )

        assert "radiohead::creep" in result
        assert result["radiohead::creep"].id == 10
        assert metrics.reused == 1
        assert metrics.created == 0

        # Should have created a connector mapping with CANONICAL_REUSE method
        connector_repo.map_track_to_connector.assert_called_once()
        call_args = connector_repo.map_track_to_connector.call_args
        assert call_args.args[1] == "lastfm"
        assert call_args.args[3] == "canonical_reuse"

        # No API calls needed — no skeletal track creation
        lastfm_client.get_track_info_comprehensive.assert_not_called()
        track_repo.save_track.assert_not_called()

    async def test_no_reuse_when_no_title_artist_match(self):
        """When no existing track matches title+artist, fall through to track creation."""
        lastfm_client = AsyncMock()
        lastfm_client.get_track_info_comprehensive.return_value = MagicMock(
            lastfm_url="https://www.last.fm/music/Radiohead/_/Creep",
            lastfm_duration=238000,
            lastfm_album_name="Pablo Honey",
        )

        saved_track = make_track(id=42)
        resolver = LastfmInwardResolver(
            lastfm_client=lastfm_client,
        )

        uow = _make_uow(saved_track=saved_track)
        track_repo = uow.get_track_repository()
        connector_repo = uow.get_connector_repository()

        # Mapping Lookup: no connector mapping
        connector_repo.find_tracks_by_connectors.return_value = {}
        # Canonical Reuse: no title+artist match
        track_repo.find_tracks_by_title_artist.return_value = {}

        result, metrics = await resolver.resolve_to_canonical_tracks(
            ["radiohead::creep"], uow
        )

        assert "radiohead::creep" in result
        assert metrics.reused == 0
        assert metrics.created == 1

        # Should have called track.getInfo for the new track
        lastfm_client.get_track_info_comprehensive.assert_called_once()

    async def test_reuse_mixed_with_existing_and_new(self):
        """Mapping lookup, canonical reuse, and track creation all resolve different IDs."""
        existing_via_mapping = make_track(id=1, title="Existing", artist="Band A")
        existing_via_reuse = make_track(id=2, title="Reused", artist="Band B")
        created_new = make_track(id=3, title="New", artist="Band C")

        lastfm_client = AsyncMock()
        lastfm_client.get_track_info_comprehensive.return_value = MagicMock(
            lastfm_url="https://www.last.fm/music/Band+C/_/New",
            lastfm_duration=200000,
            lastfm_album_name=None,
        )

        resolver = LastfmInwardResolver(
            lastfm_client=lastfm_client,
        )

        uow = _make_uow(saved_track=created_new)
        track_repo = uow.get_track_repository()
        connector_repo = uow.get_connector_repository()

        # Mapping Lookup: one found via connector mapping
        connector_repo.find_tracks_by_connectors.return_value = {
            ("lastfm", "band a::existing"): existing_via_mapping,
        }
        # Canonical Reuse: one found via title+artist
        track_repo.find_tracks_by_title_artist.return_value = {
            ("reused", "band b"): existing_via_reuse,
        }

        result, metrics = await resolver.resolve_to_canonical_tracks(
            ["band a::existing", "band b::reused", "band c::new"], uow
        )

        assert len(result) == 3
        assert metrics.existing == 1
        assert metrics.reused == 1
        assert metrics.created == 1

    async def test_reuse_rejected_when_evaluation_fails(self):
        """Canonical reuse should reject candidates that fail match evaluation.

        A candidate with very different title (e.g. live version) should be
        rejected by the evaluation service even if the DB query returned it.
        """
        # DB returns a candidate with a very different title
        wrong_candidate = make_track(
            id=99, title="Completely Different Song", artist="Radiohead"
        )

        lastfm_client = AsyncMock()
        lastfm_client.get_track_info_comprehensive.return_value = MagicMock(
            lastfm_url="https://www.last.fm/music/Radiohead/_/Creep",
            lastfm_duration=238000,
            lastfm_album_name="Pablo Honey",
        )

        resolver = LastfmInwardResolver(
            lastfm_client=lastfm_client,
        )

        saved_track = make_track(id=42)
        uow = _make_uow(saved_track=saved_track)
        track_repo = uow.get_track_repository()
        connector_repo = uow.get_connector_repository()

        connector_repo.find_tracks_by_connectors.return_value = {}
        # DB query somehow returned a bad candidate (wrong title)
        track_repo.find_tracks_by_title_artist.return_value = {
            ("creep", "radiohead"): wrong_candidate,
        }

        result, metrics = await resolver.resolve_to_canonical_tracks(
            ["radiohead::creep"], uow
        )

        # Should reject the candidate and fall through to track creation
        assert metrics.reused == 0
        assert metrics.created == 1
        assert result["radiohead::creep"].id == 42  # Track creation created track
