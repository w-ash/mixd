"""Tests for LastfmInwardResolver.

Validates that the Last.fm-specific inward resolver enriches via track.getInfo
into an in-memory probe, mints connector IDs on the normalized artist::title
composite (from Last.fm-CORRECTED names, with a raw-alias secondary mapping
when corrected differs from raw — v0.8.18 FM4a), builds the canonical once
(reuse-before-create), and drives cross-service discovery via the
CrossDiscoveryProvider protocol's ``discover`` → ``DiscoveryOutcome`` contract.
"""

from unittest.mock import AsyncMock, MagicMock

from src.config.constants import MatchMethod
from src.domain.entities import Track
from src.domain.matching.protocols import NewMapping, Nothing
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


class TestCreatesEnrichedTrack:
    """New tracks should be built once and enriched via track.getInfo."""

    async def test_creates_track_and_uses_composite_connector_id(self):
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

        saved_track = make_track(id=42)
        resolver = LastfmInwardResolver(
            lastfm_client=lastfm_client,
            cross_discovery=cross_discovery,
        )

        uow = _make_uow(saved_track=saved_track)
        result, metrics = await resolver.resolve_to_canonical_tracks(
            ["radiohead::creep"], uow, user_id="test-user"
        )

        assert "radiohead::creep" in result
        assert result["radiohead::creep"].id == 42
        assert metrics.created == 1

        # Verify the connector mapping uses the normalized composite, not the URL
        map_calls = uow.get_connector_repository().map_track_to_connector.call_args_list
        lastfm_calls = [c for c in map_calls if c.args[1] == "lastfm"]
        connector_ids = [c.args[2] for c in lastfm_calls]
        assert connector_ids == [make_lastfm_identifier("Radiohead", "Creep")]
        assert lastfm_url not in connector_ids


class TestCrossDiscovery:
    """Cross-discovery provider should be called for each new track."""

    async def test_successful_discovery_calls_provider(self):
        lastfm_client = AsyncMock()
        lastfm_client.get_track_info_comprehensive.return_value = MagicMock(
            lastfm_url="https://www.last.fm/music/Radiohead/_/Creep",
            lastfm_duration=238000,
            lastfm_album_name="Pablo Honey",
            lastfm_mbid=None,
            lastfm_artist_name="Radiohead",
            lastfm_title="Creep",
        )

        cross_discovery = AsyncMock()
        cross_discovery.discover.return_value = NewMapping(
            spotify_id="spotify123",
            confidence=90,
            match_method=MatchMethod.LASTFM_DISCOVERY,
        )

        saved_track = make_track(id=42)
        resolver = LastfmInwardResolver(
            lastfm_client=lastfm_client,
            cross_discovery=cross_discovery,
        )

        uow = _make_uow(saved_track=saved_track)
        result, metrics = await resolver.resolve_to_canonical_tracks(
            ["radiohead::creep"], uow, user_id="test-user"
        )

        # Should have called discover with the probe + artist/title positionally.
        cross_discovery.discover.assert_called_once()
        call_args = cross_discovery.discover.call_args
        assert call_args.args[1] == "radiohead"  # artist_name
        assert call_args.args[2] == "creep"  # track_name
        # The first positional arg is the UNSAVED in-memory probe track.
        probe = call_args.args[0]
        assert isinstance(probe, Track)
        assert call_args.kwargs["user_id"] == "test-user"

    async def test_no_discovery_when_provider_is_none(self):
        lastfm_client = AsyncMock()
        lastfm_client.get_track_info_comprehensive.return_value = MagicMock(
            lastfm_url="https://www.last.fm/music/Radiohead/_/Creep",
            lastfm_duration=238000,
            lastfm_album_name="Pablo Honey",
            lastfm_mbid=None,
            lastfm_artist_name="Radiohead",
            lastfm_title="Creep",
        )

        saved_track = make_track(id=42)
        resolver = LastfmInwardResolver(
            lastfm_client=lastfm_client,
            cross_discovery=None,
        )

        uow = _make_uow(saved_track=saved_track)
        result, metrics = await resolver.resolve_to_canonical_tracks(
            ["radiohead::creep"], uow, user_id="test-user"
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
            lastfm_mbid=None,
            lastfm_artist_name="Radiohead",
            lastfm_title="Creep",
        )

        cross_discovery = AsyncMock()
        cross_discovery.discover.return_value = Nothing()

        saved_track = make_track(id=42)
        resolver = LastfmInwardResolver(
            lastfm_client=lastfm_client,
            cross_discovery=cross_discovery,
        )

        uow = _make_uow(saved_track=saved_track)
        result, metrics = await resolver.resolve_to_canonical_tracks(
            ["radiohead::creep"], uow, user_id="test-user"
        )

        # Track should still be created
        assert "radiohead::creep" in result


class TestTrackInfoFailure:
    """When track.getInfo fails, fallback artist::title connector ID is used."""

    async def test_enrichment_failure_uses_fallback_id(self):
        lastfm_client = AsyncMock()
        lastfm_client.get_track_info_comprehensive.return_value = None  # Failure
        # getCorrection also has nothing on file — degrades to raw names.
        lastfm_client.get_track_correction.return_value = None

        cross_discovery = AsyncMock()
        cross_discovery.discover.return_value = Nothing()

        saved_track = make_track(id=42)
        resolver = LastfmInwardResolver(
            lastfm_client=lastfm_client,
            cross_discovery=cross_discovery,
        )

        uow = _make_uow(saved_track=saved_track)
        result, metrics = await resolver.resolve_to_canonical_tracks(
            ["radiohead::creep"], uow, user_id="test-user"
        )

        assert "radiohead::creep" in result

        # Connector ID should be the fallback format (no URL available)
        map_calls = uow.get_connector_repository().map_track_to_connector.call_args_list
        lastfm_calls = [c for c in map_calls if c.args[1] == "lastfm"]
        connector_ids = [c.args[2] for c in lastfm_calls]
        # Should use artist::title fallback
        assert any("::" in cid and "last.fm" not in cid for cid in connector_ids)


class TestMBIDEnrichment:
    """Last.fm's getInfo MBID must be quarantined, not used as a musicbrainz identity."""

    async def test_enrichment_quarantines_lastfm_mbid(self):
        """A getInfo MBID must NOT become a musicbrainz connector identifier.

        Last.fm returns an untrusted track-level MBID from its own matching
        (LB-431). Writing it into connector_track_identifiers['musicbrainz']
        would feed save_track's uq_tracks_user_mbid merge key and could collapse
        two distinct recordings, so the resolver quarantines it (FM1d). Album and
        duration enrichment still apply.
        """
        lastfm_client = AsyncMock()
        test_mbid = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        lastfm_client.get_track_info_comprehensive.return_value = MagicMock(
            lastfm_url="https://www.last.fm/music/Radiohead/_/Creep",
            lastfm_duration=238000,
            lastfm_album_name="Pablo Honey",
            lastfm_mbid=test_mbid,
            lastfm_artist_name="Radiohead",
            lastfm_title="Creep",
        )

        cross_discovery = AsyncMock()
        cross_discovery.discover.return_value = Nothing()

        saved_track = make_track(id=42)
        resolver = LastfmInwardResolver(
            lastfm_client=lastfm_client,
            cross_discovery=cross_discovery,
        )

        uow = _make_uow(saved_track=saved_track)
        result, _ = await resolver.resolve_to_canonical_tracks(
            ["radiohead::creep"], uow, user_id="test-user"
        )

        assert "radiohead::creep" in result

        track_repo = uow.get_track_repository()
        save_calls = track_repo.save_track.call_args_list
        assert save_calls, "expected the enriched probe to be saved"
        # The untrusted Last.fm MBID must never reach the musicbrainz identity slot.
        for c in save_calls:
            probe = c.args[0]
            assert "musicbrainz" not in probe.connector_track_identifiers
        # ...but non-identity enrichment (album, duration) still lands.
        assert any(c.args[0].duration_ms == 238000 for c in save_calls)

    async def test_no_mbid_when_track_info_has_none(self):
        """When track.getInfo returns no MBID, connector_track_identifiers is unchanged."""
        lastfm_client = AsyncMock()
        lastfm_client.get_track_info_comprehensive.return_value = MagicMock(
            lastfm_url="https://www.last.fm/music/Radiohead/_/Creep",
            lastfm_duration=238000,
            lastfm_album_name="Pablo Honey",
            lastfm_mbid=None,
            lastfm_artist_name="Radiohead",
            lastfm_title="Creep",
        )

        cross_discovery = AsyncMock()
        cross_discovery.discover.return_value = Nothing()

        saved_track = make_track(id=42)
        resolver = LastfmInwardResolver(
            lastfm_client=lastfm_client,
            cross_discovery=cross_discovery,
        )

        uow = _make_uow(saved_track=saved_track)
        result, _ = await resolver.resolve_to_canonical_tracks(
            ["radiohead::creep"], uow, user_id="test-user"
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
            ["radiohead::creep"], uow, user_id="test-user"
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
            ["radiohead::creep"], uow, user_id="test-user"
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
            lastfm_mbid=None,
            lastfm_artist_name="Radiohead",
            lastfm_title="Creep",
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
            ["radiohead::creep"], uow, user_id="test-user"
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
            lastfm_mbid=None,
            lastfm_artist_name="Band C",
            lastfm_title="New",
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
            ["band a::existing", "band b::reused", "band c::new"],
            uow,
            user_id="test-user",
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
            lastfm_mbid=None,
            lastfm_artist_name="Radiohead",
            lastfm_title="Creep",
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
            ["radiohead::creep"], uow, user_id="test-user"
        )

        # Should reject the candidate and fall through to track creation
        assert metrics.reused == 0
        assert metrics.created == 1
        assert result["radiohead::creep"].id == 42  # Track creation created track


class TestCorrectedNameDualMapping:
    """FM4a: when Last.fm's autocorrect differs from the raw identifier, mint
    a PRIMARY mapping on the corrected composite AND a SECONDARY
    ``LASTFM_RAW_ALIAS`` mapping on the raw composite — both on the SAME
    canonical — so a future raw-spelled import still hits the fast
    connector-mapping lookup instead of re-running getInfo/getCorrection."""

    async def test_corrected_name_primary_and_raw_name_secondary_alias(self):
        lastfm_client = AsyncMock()
        # autocorrect=1 fixes the misspelled raw artist "Led Zepplin".
        lastfm_client.get_track_info_comprehensive.return_value = MagicMock(
            lastfm_url="https://www.last.fm/music/Led+Zeppelin/_/Stairway+to+Heaven",
            lastfm_duration=482000,
            lastfm_album_name="Led Zeppelin IV",
            lastfm_mbid=None,
            lastfm_artist_name="Led Zeppelin",
            lastfm_title="Stairway to Heaven",
        )

        cross_discovery = AsyncMock()
        cross_discovery.discover.return_value = Nothing()

        saved_track = make_track(id=42)
        resolver = LastfmInwardResolver(
            lastfm_client=lastfm_client,
            cross_discovery=cross_discovery,
        )

        uow = _make_uow(saved_track=saved_track)
        result, metrics = await resolver.resolve_to_canonical_tracks(
            ["led zepplin::stairway to heaven"], uow, user_id="test-user"
        )

        assert "led zepplin::stairway to heaven" in result
        assert metrics.created == 1

        corrected_key = make_lastfm_identifier("Led Zeppelin", "Stairway to Heaven")
        raw_key = make_lastfm_identifier("led zepplin", "stairway to heaven")
        assert corrected_key != raw_key  # sanity: the two really do differ

        map_calls = uow.get_connector_repository().map_track_to_connector.call_args_list
        lastfm_calls = [c for c in map_calls if c.args[1] == "lastfm"]
        methods_by_id = {c.args[2]: c.args[3] for c in lastfm_calls}

        assert methods_by_id == {
            corrected_key: MatchMethod.LASTFM_IMPORT,
            raw_key: MatchMethod.LASTFM_RAW_ALIAS,
        }

        # The raw alias is minted with auto_set_primary=False so it never demotes
        # the corrected import primary (last-write-wins would otherwise promote it
        # and report "Secondary Cache" as the track's provenance — v0.8.18 FM1b).
        primary_by_id = {c.args[2]: c.kwargs["auto_set_primary"] for c in lastfm_calls}
        assert primary_by_id == {corrected_key: True, raw_key: False}

        # Both mappings target the SAME canonical track.
        assert all(c.args[0] == saved_track for c in lastfm_calls)

    async def test_corrected_name_equals_raw_mints_only_primary(self):
        """When autocorrect only changes case (normalizes to the same
        composite), no secondary alias mapping is minted — a single mapping,
        matching the pre-4a single-scheme case."""
        lastfm_client = AsyncMock()
        lastfm_client.get_track_info_comprehensive.return_value = MagicMock(
            lastfm_url="https://www.last.fm/music/Radiohead/_/Creep",
            lastfm_duration=238000,
            lastfm_album_name="Pablo Honey",
            lastfm_mbid=None,
            lastfm_artist_name="Radiohead",
            lastfm_title="Creep",
        )

        cross_discovery = AsyncMock()
        cross_discovery.discover.return_value = Nothing()

        saved_track = make_track(id=42)
        resolver = LastfmInwardResolver(
            lastfm_client=lastfm_client,
            cross_discovery=cross_discovery,
        )

        uow = _make_uow(saved_track=saved_track)
        result, metrics = await resolver.resolve_to_canonical_tracks(
            ["radiohead::creep"], uow, user_id="test-user"
        )

        assert "radiohead::creep" in result
        assert metrics.created == 1

        map_calls = uow.get_connector_repository().map_track_to_connector.call_args_list
        lastfm_calls = [c for c in map_calls if c.args[1] == "lastfm"]
        assert len(lastfm_calls) == 1
        assert lastfm_calls[0].args[2] == make_lastfm_identifier("Radiohead", "Creep")
        assert lastfm_calls[0].args[3] == MatchMethod.LASTFM_IMPORT


class TestCanonicalDisplayCasing:
    """Canonical rows are minted from CORRECTED display names, not the
    lowercased identifier parts (convergence findings §5b: lowercase twins).
    """

    async def test_probe_preserves_display_casing_on_creation(self):
        lastfm_client = AsyncMock()
        lastfm_client.get_track_info_comprehensive.return_value = MagicMock(
            lastfm_url="https://www.last.fm/music/Carwash/_/Striptease",
            lastfm_duration=201000,
            lastfm_album_name="Shimmer",
            lastfm_mbid=None,
            lastfm_artist_name="Carwash",
            lastfm_title="Striptease",
        )

        saved_track = make_track(id=42)
        resolver = LastfmInwardResolver(lastfm_client=lastfm_client)

        uow = _make_uow(saved_track=saved_track)
        result, _metrics = await resolver.resolve_to_canonical_tracks(
            ["carwash::striptease"], uow, user_id="test-user"
        )

        assert "carwash::striptease" in result
        saved_probe = uow.get_track_repository().save_track.call_args.args[0]
        assert saved_probe.title == "Striptease"
        assert [a.name for a in saved_probe.artists] == ["Carwash"]

    async def test_probe_degrades_to_raw_names_when_correction_unavailable(self):
        """getInfo AND getCorrection failing leaves the raw lowercased names —
        the accepted residual documented on _build_enriched_probe."""
        lastfm_client = AsyncMock()
        lastfm_client.get_track_info_comprehensive.return_value = None
        lastfm_client.get_track_correction.return_value = None

        saved_track = make_track(id=42)
        resolver = LastfmInwardResolver(lastfm_client=lastfm_client)

        uow = _make_uow(saved_track=saved_track)
        _ = await resolver.resolve_to_canonical_tracks(
            ["carwash::striptease"], uow, user_id="test-user"
        )

        saved_probe = uow.get_track_repository().save_track.call_args.args[0]
        assert saved_probe.title == "striptease"
        assert [a.name for a in saved_probe.artists] == ["carwash"]
