"""Tests for LastfmInwardResolver.

Validates the three-phase creation path: concurrent track.getInfo enrichment
into in-memory probes (corrected names mint the connector ids — v0.8.18 FM4a,
with a raw-alias secondary when corrected differs from raw), sequential
cross-service discovery via the CrossDiscoveryProvider protocol's ``discover``
→ ``DiscoveryOutcome`` contract, and the pure plan → chunk-bulk persist
(one ``save_tracks`` + one ``map_tracks_to_connectors``, per-item savepoint
fallback on bulk failure).
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx2

from src.config import settings
from src.config.constants import MatchMethod
from src.domain.entities import Track
from src.domain.matching.protocols import NewMapping, Nothing
from src.domain.repositories.connector import ConnectorMappingSpec
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
    """Create a mock UoW with configured repositories.

    The chunk-bulk persist saves a whole chunk of canonicals in one
    ``save_tracks`` call. Route it through whatever ``save_track`` is
    configured with, so a test that cares about saved payloads still only
    has to configure one seam — and so the per-item fallback and the bulk
    path agree by construction.
    """
    default_track = saved_track or make_track(title="Creep", artist="Radiohead")
    uow = make_mock_uow()

    track_repo = uow.get_track_repository()
    track_repo.save_track.return_value = default_track

    async def _save_tracks(tracks):
        return [await track_repo.save_track(track) for track in tracks]

    track_repo.save_tracks.side_effect = _save_tracks
    # Canonical Reuse: default to no title+artist matches
    track_repo.find_tracks_by_title_artist.return_value = {}

    connector_repo = uow.get_connector_repository()
    connector_repo.find_tracks_by_connectors.return_value = existing_tracks or {}
    connector_repo.map_tracks_to_connectors.side_effect = lambda specs: [
        spec.track for spec in specs
    ]

    return uow


def _mapping_specs(uow) -> list[ConnectorMappingSpec]:
    """Every mapping the chunk-bulk persist asked for, in order."""
    return [
        spec
        for c in uow.get_connector_repository().map_tracks_to_connectors.call_args_list
        for spec in c.args[0]
    ]


def _lastfm_specs(uow) -> list[ConnectorMappingSpec]:
    return [spec for spec in _mapping_specs(uow) if spec.connector == "lastfm"]


def _track_info(**overrides) -> MagicMock:
    defaults = {
        "lastfm_url": "https://www.last.fm/music/Radiohead/_/Creep",
        "lastfm_duration": 238000,
        "lastfm_album_name": "Pablo Honey",
        "lastfm_mbid": None,
        "lastfm_artist_name": "Radiohead",
        "lastfm_title": "Creep",
    }
    defaults.update(overrides)
    return MagicMock(**defaults)


class TestCreatesEnrichedTrack:
    """New tracks should be built once and enriched via track.getInfo."""

    async def test_creates_track_and_uses_composite_connector_id(self):
        lastfm_client = AsyncMock()
        lastfm_url = "https://www.last.fm/music/Radiohead/_/Creep"
        lastfm_client.get_track_info_comprehensive.return_value = _track_info()

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
        connector_ids = [spec.connector_id for spec in _lastfm_specs(uow)]
        assert connector_ids == [make_lastfm_identifier("Radiohead", "Creep")]
        assert lastfm_url not in connector_ids


class TestCrossDiscovery:
    """Cross-discovery provider should be called for each new track."""

    async def test_successful_discovery_calls_provider(self):
        lastfm_client = AsyncMock()
        lastfm_client.get_track_info_comprehensive.return_value = _track_info()

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

        # The discovered Spotify id lands as a primary spec in the same batch.
        spotify_specs = [s for s in _mapping_specs(uow) if s.connector == "spotify"]
        assert [s.connector_id for s in spotify_specs] == ["spotify123"]
        assert spotify_specs[0].primary is True
        assert spotify_specs[0].match_method == MatchMethod.LASTFM_DISCOVERY

    async def test_no_discovery_when_provider_is_none(self):
        lastfm_client = AsyncMock()
        lastfm_client.get_track_info_comprehensive.return_value = _track_info()

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

    async def test_failed_discovery_degrades_to_nothing_and_still_creates(self):
        """A discovery failure is not a failed identifier: its savepoint rolls
        back alone and the identifier proceeds to creation without a Spotify
        mapping."""
        lastfm_client = AsyncMock()
        lastfm_client.get_track_info_comprehensive.return_value = _track_info()

        cross_discovery = AsyncMock()
        cross_discovery.discover.side_effect = RuntimeError(
            "current transaction is aborted"
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

        assert result["radiohead::creep"].id == 42
        assert metrics.created == 1
        assert metrics.failed == 0
        assert [s.connector for s in _mapping_specs(uow)] == ["lastfm"]


class TestDiscoveryRejected:
    """When cross-discovery returns Nothing, track is still created."""

    async def test_failed_discovery_still_creates_track(self):
        lastfm_client = AsyncMock()
        lastfm_client.get_track_info_comprehensive.return_value = _track_info(
            lastfm_album_name=None
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
        connector_ids = [spec.connector_id for spec in _lastfm_specs(uow)]
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
        lastfm_client.get_track_info_comprehensive.return_value = _track_info(
            lastfm_mbid=test_mbid
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
        lastfm_client.get_track_info_comprehensive.return_value = _track_info()

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

        # Should have created a connector mapping with CANONICAL_REUSE method,
        # holding primacy (the single-mapping call's auto_set_primary default).
        specs = _mapping_specs(uow)
        assert len(specs) == 1
        assert specs[0].connector == "lastfm"
        assert specs[0].match_method == MatchMethod.CANONICAL_REUSE
        assert specs[0].primary is True

        # No API calls needed — no skeletal track creation
        lastfm_client.get_track_info_comprehensive.assert_not_called()
        track_repo.save_track.assert_not_called()

    async def test_no_reuse_when_no_title_artist_match(self):
        """When no existing track matches title+artist, fall through to track creation."""
        lastfm_client = AsyncMock()
        lastfm_client.get_track_info_comprehensive.return_value = _track_info()

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
        lastfm_client.get_track_info_comprehensive.return_value = _track_info(
            lastfm_url="https://www.last.fm/music/Band+C/_/New",
            lastfm_duration=200000,
            lastfm_album_name=None,
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
        lastfm_client.get_track_info_comprehensive.return_value = _track_info()

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
        lastfm_client.get_track_info_comprehensive.return_value = _track_info(
            lastfm_url="https://www.last.fm/music/Led+Zeppelin/_/Stairway+to+Heaven",
            lastfm_duration=482000,
            lastfm_album_name="Led Zeppelin IV",
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

        lastfm_specs = _lastfm_specs(uow)
        methods_by_id = {spec.connector_id: spec.match_method for spec in lastfm_specs}

        assert methods_by_id == {
            corrected_key: MatchMethod.LASTFM_IMPORT,
            raw_key: MatchMethod.LASTFM_RAW_ALIAS,
        }

        # The raw alias is minted with primary=False so it never demotes the
        # corrected import primary (last-write-wins would otherwise promote it
        # and report "Secondary Cache" as the track's provenance — v0.8.18 FM1b).
        primary_by_id = {spec.connector_id: spec.primary for spec in lastfm_specs}
        assert primary_by_id == {corrected_key: True, raw_key: False}

        # Both mappings target the SAME canonical track.
        assert all(spec.track == saved_track for spec in lastfm_specs)

    async def test_corrected_name_equals_raw_mints_only_primary(self):
        """When autocorrect only changes case (normalizes to the same
        composite), no secondary alias mapping is minted — a single mapping,
        matching the pre-4a single-scheme case."""
        lastfm_client = AsyncMock()
        lastfm_client.get_track_info_comprehensive.return_value = _track_info()

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

        lastfm_specs = _lastfm_specs(uow)
        assert len(lastfm_specs) == 1
        assert lastfm_specs[0].connector_id == make_lastfm_identifier(
            "Radiohead", "Creep"
        )
        assert lastfm_specs[0].match_method == MatchMethod.LASTFM_IMPORT


class TestCanonicalDisplayCasing:
    """Canonical rows are minted from CORRECTED display names, not the
    lowercased identifier parts (convergence findings §5b: lowercase twins).
    """

    async def test_probe_preserves_display_casing_on_creation(self):
        lastfm_client = AsyncMock()
        lastfm_client.get_track_info_comprehensive.return_value = _track_info(
            lastfm_url="https://www.last.fm/music/Carwash/_/Striptease",
            lastfm_duration=201000,
            lastfm_album_name="Shimmer",
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


class TestConcurrentEnrichment:
    """Phase A: getInfo runs concurrently, bounded, order-preserving, and one
    failure degrades its own identifier instead of cancelling the chunk."""

    async def test_semaphore_bounds_inflight_enrichment(self, monkeypatch):
        monkeypatch.setattr(settings.api.lastfm, "concurrency", 2)

        inflight = 0
        max_inflight = 0

        async def _slow_get_info(artist, title):
            nonlocal inflight, max_inflight
            inflight += 1
            max_inflight = max(max_inflight, inflight)
            await asyncio.sleep(0.01)
            inflight -= 1

        lastfm_client = AsyncMock()
        lastfm_client.get_track_info_comprehensive.side_effect = _slow_get_info
        lastfm_client.get_track_correction.return_value = None

        resolver = LastfmInwardResolver(lastfm_client=lastfm_client)
        uow = _make_uow()

        identifiers = [f"artist {n}::track {n}" for n in range(6)]
        result, metrics = await resolver.resolve_to_canonical_tracks(
            identifiers, uow, user_id="test-user"
        )

        assert lastfm_client.get_track_info_comprehensive.await_count == 6
        assert metrics.created == 6
        assert max_inflight <= 2
        # It actually ran concurrently — a sequential loop would peak at 1.
        assert max_inflight == 2

    async def test_results_restore_identifier_order_not_completion_order(self):
        """The first identifier finishes LAST; the plan and persist must still
        follow input order — planning and persistence are deterministic."""
        delays = {"aa": 0.03, "bb": 0.0, "cc": 0.01}

        async def _staggered_get_info(artist, title):
            await asyncio.sleep(delays[artist])
            return _track_info(
                lastfm_artist_name=artist.upper(), lastfm_title=title.upper()
            )

        lastfm_client = AsyncMock()
        lastfm_client.get_track_info_comprehensive.side_effect = _staggered_get_info

        resolver = LastfmInwardResolver(lastfm_client=lastfm_client)
        uow = _make_uow()

        _ = await resolver._create_tracks_batch(
            ["aa::t1", "bb::t2", "cc::t3"], uow, user_id="test-user"
        )

        # save_tracks receives the chunk's probes in identifier order.
        saved_probes = uow.get_track_repository().save_tracks.call_args.args[0]
        assert [a.name for probe in saved_probes for a in probe.artists] == [
            "AA",
            "BB",
            "CC",
        ]

    async def test_one_failed_enrichment_fails_without_cancelling_siblings(self):
        """An exception escaping the probe builder is caught in the task body —
        a TaskGroup would otherwise cancel every sibling enrichment — and the
        identifier FAILS instead of degrading (v0.10.2.9 F5: content misses
        return ``None`` and never raise, so any raise is a transport-shaped
        failure that must not mint a canonical from raw names)."""
        real_build = LastfmInwardResolver._build_enriched_probe

        async def _explosive_build(self, artist_name, track_name, *, user_id):
            if artist_name == "bad artist":
                raise RuntimeError("unexpected payload shape")
            return await real_build(self, artist_name, track_name, user_id=user_id)

        lastfm_client = AsyncMock()
        lastfm_client.get_track_info_comprehensive.return_value = _track_info(
            lastfm_artist_name="Good Artist", lastfm_title="Good Track"
        )

        resolver = LastfmInwardResolver(lastfm_client=lastfm_client)
        uow = _make_uow()

        with patch.object(
            LastfmInwardResolver, "_build_enriched_probe", new=_explosive_build
        ):
            result, metrics = await resolver.resolve_to_canonical_tracks(
                ["bad artist::bad track", "good artist::good track"],
                uow,
                user_id="test-user",
            )

        assert metrics.created == 1
        assert metrics.failed == 1
        assert "good artist::good track" in result
        assert "bad artist::bad track" not in result
        # No canonical was minted from the failed identifier's raw names.
        saved_titles = [
            c.args[0].title
            for c in uow.get_track_repository().save_track.call_args_list
        ]
        assert saved_titles == ["Good Track"]


class TestCreationFailureIsolation:
    """One failed identifier must not poison the rest of the batch.

    Pins the v0.10.2.2 fix in its chunk-bulk shape: the chunk persists under
    one savepoint, and when that bulk attempt fails it is rewritten one
    savepoint per identifier, so a SQL failure rolls back alone instead of
    aborting the transaction for every remaining identifier
    (``InFailedSqlTransaction`` cascade).
    """

    @staticmethod
    def _poison_mapping_writes(uow, bad_connector_id: str) -> None:
        """Refuse any mapping batch carrying the poisoned connector id."""

        async def _refuse(specs):
            if any(spec.connector_id == bad_connector_id for spec in specs):
                raise RuntimeError("boom")
            return [spec.track for spec in specs]

        uow.get_connector_repository().map_tracks_to_connectors.side_effect = _refuse

    async def test_failed_identifier_does_not_stop_the_batch(self):
        lastfm_client = AsyncMock()
        lastfm_client.get_track_info_comprehensive.return_value = None
        lastfm_client.get_track_correction.return_value = None

        resolver = LastfmInwardResolver(lastfm_client=lastfm_client)
        uow = _make_uow(saved_track=make_track(id=7))
        self._poison_mapping_writes(uow, "bad::track")

        result, metrics = await resolver.resolve_to_canonical_tracks(
            ["bad::track", "good::track"], uow, user_id="test-user"
        )

        assert "good::track" in result
        assert "bad::track" not in result
        assert metrics.created == 1
        assert metrics.failed == 1
        # One savepoint for the failed bulk attempt, then one per identifier —
        # the isolation itself.
        assert uow.savepoint.call_count == 3

    async def test_failed_reuse_mapping_does_not_create_a_duplicate_canonical(self):
        """The matcher already accepted an existing canonical for this
        identifier, so a rolled-back reuse mapping must not fall through to
        creation — that would mint a second canonical for the same recording."""
        lastfm_client = AsyncMock()
        existing = make_track(id=42, title="Creep", artist="Radiohead")

        resolver = LastfmInwardResolver(lastfm_client=lastfm_client)
        uow = _make_uow()
        track_repo = uow.get_track_repository()
        track_repo.find_tracks_by_title_artist.return_value = {
            ("creep", "radiohead"): existing
        }
        uow.get_connector_repository().map_tracks_to_connectors.side_effect = (
            RuntimeError("duplicate key value violates unique constraint")
        )

        result, metrics = await resolver.resolve_to_canonical_tracks(
            ["radiohead::creep"], uow, user_id="test-user"
        )

        assert result == {}
        track_repo.save_track.assert_not_called()
        lastfm_client.get_track_info_comprehensive.assert_not_called()
        assert metrics.reused == 0
        assert metrics.created == 0
        assert metrics.failed == 1


class TestChunkBulkPersistIsOneRoundTripGroup:
    """The whole point: a chunk's writes are batch calls, not per-id calls."""

    async def test_a_five_id_chunk_makes_one_save_and_one_mapping_call(self):
        lastfm_client = AsyncMock()
        lastfm_client.get_track_info_comprehensive.return_value = None
        lastfm_client.get_track_correction.return_value = None

        resolver = LastfmInwardResolver(lastfm_client=lastfm_client)
        uow = _make_uow()

        identifiers = [f"artist {n}::track {n}" for n in range(5)]
        result, metrics = await resolver.resolve_to_canonical_tracks(
            identifiers, uow, user_id="test-user"
        )

        assert metrics.created == 5
        assert len(result) == 5
        track_repo = uow.get_track_repository()
        track_repo.save_tracks.assert_awaited_once()
        assert len(track_repo.save_tracks.await_args.args[0]) == 5
        connector_repo = uow.get_connector_repository()
        connector_repo.map_tracks_to_connectors.assert_awaited_once()
        assert len(connector_repo.map_tracks_to_connectors.await_args.args[0]) == 5
        connector_repo.map_track_to_connector.assert_not_called()
        # One savepoint for the chunk, not one per identifier.
        assert uow.savepoint.call_count == 1


class TestCanonicalPayloadTenancy:
    """Mirror of the Spotify resolver's tenancy pin (v0.10.2.9).

    Every probe handed to ``save_tracks`` must carry the caller's tenant —
    a probe built without ``user_id`` falls back to Track's silent
    ``"default"`` and mistenants the created canonical.
    """

    async def test_saved_probes_carry_the_tenant(self):
        lastfm_client = AsyncMock()
        lastfm_client.get_track_info_comprehensive.return_value = _track_info()

        resolver = LastfmInwardResolver(lastfm_client=lastfm_client)
        uow = _make_uow()

        _ = await resolver._create_tracks_batch(
            ["Radiohead::Creep"], uow, user_id="TENANT_A"
        )

        saved = [
            track
            for save_call in uow.get_track_repository().save_tracks.call_args_list
            for track in save_call.args[0]
        ]
        assert saved
        assert {track.user_id for track in saved} == {"TENANT_A"}


class TestTransportFailureDoesNotMintCanonicals:
    """v0.10.2.9 F5: an outage must not mint permanent junk canonicals.

    The client raises (httpx2 errors, retry-exhausted LastFMAPIError) for
    TRANSPORT failures and returns ``None`` only for content misses. A raised
    failure marks the identifier failed — absent from results and persist,
    counted in the failed metric — so the play stays unresolved and the next
    import retries enrichment. A content miss still degrades to the raw-name
    probe (pinned in TestCanonicalDisplayCasing).
    """

    async def test_transport_failure_fails_the_identifier_without_a_probe(self):
        lastfm_client = AsyncMock()
        lastfm_client.get_track_info_comprehensive.side_effect = httpx2.ConnectError(
            "connection refused"
        )

        resolver = LastfmInwardResolver(lastfm_client=lastfm_client)
        uow = _make_uow()

        result, metrics = await resolver.resolve_to_canonical_tracks(
            ["radiohead::creep"], uow, user_id="test-user"
        )

        assert result == {}
        assert metrics.failed == 1
        assert metrics.created == 0
        uow.get_track_repository().save_track.assert_not_called()
        assert _mapping_specs(uow) == []

    async def test_transport_failure_in_correction_fallback_also_fails(self):
        # getInfo answered "nothing" (content), then the outage hit the
        # correction fallback — the identifier must still fail, not mint
        # from raw names.
        lastfm_client = AsyncMock()
        lastfm_client.get_track_info_comprehensive.return_value = None
        lastfm_client.get_track_correction.side_effect = httpx2.ConnectError("outage")

        resolver = LastfmInwardResolver(lastfm_client=lastfm_client)
        uow = _make_uow()

        result, metrics = await resolver.resolve_to_canonical_tracks(
            ["radiohead::creep"], uow, user_id="test-user"
        )

        assert result == {}
        assert metrics.failed == 1
        uow.get_track_repository().save_track.assert_not_called()

    async def test_sibling_identifiers_survive_one_transport_failure(self):
        # Caught in the task body: the TaskGroup must not cancel siblings,
        # and the healthy identifier's canonical is still created.
        async def _get_info(artist, title):
            if artist == "bad artist":
                raise httpx2.ConnectError("outage")
            return _track_info(
                lastfm_artist_name="Good Artist", lastfm_title="Good Track"
            )

        lastfm_client = AsyncMock()
        lastfm_client.get_track_info_comprehensive.side_effect = _get_info

        resolver = LastfmInwardResolver(lastfm_client=lastfm_client)
        uow = _make_uow(saved_track=make_track(id=7))

        result, metrics = await resolver.resolve_to_canonical_tracks(
            ["bad artist::bad track", "good artist::good track"],
            uow,
            user_id="test-user",
        )

        assert "good artist::good track" in result
        assert "bad artist::bad track" not in result
        assert metrics.created == 1
        assert metrics.failed == 1
