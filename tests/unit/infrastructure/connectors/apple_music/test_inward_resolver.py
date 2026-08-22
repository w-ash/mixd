"""Tests for AppleMusicInwardResolver — conservative ISRC-only track minting.

Validates the resolver's four outcomes for an answered catalog song (ISRC
reuse, suspect collision deferral, plain creation, no-ISRC refusal), the
playParams.catalogId dual-mapping path, backoff bookkeeping for unresolvable
ids (failed chunks excluded), and backoff suppression inherited from the
base class.
"""

from unittest.mock import AsyncMock, MagicMock, patch

from src.config.constants import MatchMethod
from src.domain.repositories.connector import ConnectorMappingSpec
from src.infrastructure.connectors.apple_music.client import CatalogSongsLookup
from src.infrastructure.connectors.apple_music.inward_resolver import (
    AppleMusicInwardResolver,
)
from tests.fixtures import attach_resolution_recorder, make_apple_song, make_track

STOREFRONT_PATCH = (
    "src.infrastructure.connectors.apple_music.inward_resolver.resolve_storefront"
)


def _make_uow():
    """UoW mock with track/connector repos and a permissive recorder wired."""
    uow = MagicMock()
    recorder = attach_resolution_recorder(uow)

    track_repo = AsyncMock()
    track_repo.find_tracks_by_isrcs.return_value = {}
    track_repo.find_tracks_by_title_artist.return_value = {}
    track_repo.save_track.return_value = make_track(1)

    async def _save_tracks(tracks):
        return [await track_repo.save_track(track) for track in tracks]

    track_repo.save_tracks.side_effect = _save_tracks
    uow.get_track_repository.return_value = track_repo

    connector_repo = AsyncMock()
    connector_repo.find_tracks_by_connectors.return_value = {}
    uow.get_connector_repository.return_value = connector_repo
    return uow, track_repo, connector_repo, recorder


def _make_resolver(songs, failed_values=None):
    client = AsyncMock()
    client.get_songs_by_ids.return_value = CatalogSongsLookup(
        songs=songs, failed_values=failed_values or []
    )
    return AppleMusicInwardResolver(client=client), client


def _mapping_specs(connector_repo) -> list[ConnectorMappingSpec]:
    return [
        spec
        for c in connector_repo.map_tracks_to_connectors.call_args_list
        for spec in c.args[0]
    ]


class TestConnectorContract:
    def test_connector_name_is_apple(self):
        resolver, _ = _make_resolver([])
        assert resolver.connector_name == "apple"

    def test_normalize_id_strips(self):
        resolver, _ = _make_resolver([])
        assert resolver._normalize_id("  1613600188 ") == "1613600188"


class TestIsrcReuse:
    async def test_isrc_hit_existing_track_maps_isrc_match(self):
        """(a) An existing canonical holding the ISRC is reused at 95."""
        existing = make_track(7, title="Held Song")
        song = make_apple_song(song_id="101", isrc="USUM72309818", duration_ms=200_000)
        resolver, _ = _make_resolver([song])
        uow, track_repo, connector_repo, _ = _make_uow()
        track_repo.find_tracks_by_isrcs.return_value = {"USUM72309818": existing}

        with patch(STOREFRONT_PATCH, AsyncMock(return_value="us")):
            result, metrics = await resolver.resolve_to_canonical_tracks(
                ["101"], uow, user_id="test-user"
            )

        assert result["101"].id == existing.id
        track_repo.save_track.assert_not_awaited()
        specs = _mapping_specs(connector_repo)
        assert len(specs) == 1
        spec = specs[0]
        assert spec.connector == "apple"
        assert spec.connector_id == "101"
        assert spec.match_method == MatchMethod.ISRC_MATCH
        assert spec.confidence == MatchMethod.ISRC_MATCH_CONFIDENCE
        assert spec.primary is True

    async def test_suspect_duration_queues_review_and_withholds_isrc(self):
        """(b) >10s duration disagreement → review queued, ISRC withheld."""
        song = make_apple_song(song_id="101", isrc="USUM72309818", duration_ms=260_000)
        resolver, _ = _make_resolver([song])
        uow, track_repo, connector_repo, _ = _make_uow()
        # Owner's duration differs by 60s — suspect.
        owner = make_track(7, duration_ms=200_000)
        track_repo.find_tracks_by_isrcs.return_value = {"USUM72309818": owner}

        with patch(STOREFRONT_PATCH, AsyncMock(return_value="us")):
            result, _ = await resolver.resolve_to_canonical_tracks(
                ["101"], uow, user_id="test-user"
            )

        assert "101" in result
        # A new canonical was minted, without the contested ISRC.
        track_repo.save_track.assert_awaited_once()
        saved = track_repo.save_track.await_args.args[0]
        assert saved.isrc is None
        # The collision review names the current ISRC owner.
        connector_repo.queue_isrc_collision_reviews.assert_awaited_once()
        collisions, service = (
            connector_repo.queue_isrc_collision_reviews.await_args.args[:2]
        )
        assert service == "apple"
        assert collisions[0].owner.id == owner.id
        assert collisions[0].connector_id == "101"
        specs = _mapping_specs(connector_repo)
        assert specs[0].match_method == MatchMethod.DIRECT_IMPORT

    async def test_isrc_without_holder_creates_direct_import(self):
        """(c) Nobody holds the ISRC → new canonical from Apple metadata."""
        song = make_apple_song(song_id="101", isrc="USUM72309818")
        resolver, _ = _make_resolver([song])
        uow, track_repo, connector_repo, _ = _make_uow()

        with patch(STOREFRONT_PATCH, AsyncMock(return_value="us")):
            result, metrics = await resolver.resolve_to_canonical_tracks(
                ["101"], uow, user_id="test-user"
            )

        assert "101" in result
        assert metrics.created == 1
        saved = track_repo.save_track.await_args.args[0]
        assert saved.isrc == "USUM72309818"
        assert saved.title == "Test Song"
        assert saved.user_id == "test-user"
        assert saved.connector_track_identifiers["apple"] == "101"
        spec = _mapping_specs(connector_repo)[0]
        assert spec.match_method == MatchMethod.DIRECT_IMPORT
        assert spec.confidence == 100
        assert spec.primary is True


class TestUnresolvable:
    async def test_song_without_isrc_mints_nothing_and_backs_off(self):
        """(d) Present but ISRC-less → no canonical, no-match backoff."""
        song = make_apple_song(song_id="101", isrc=None)
        resolver, _ = _make_resolver([song])
        uow, track_repo, _, recorder = _make_uow()

        with patch(STOREFRONT_PATCH, AsyncMock(return_value="us")):
            result, metrics = await resolver.resolve_to_canonical_tracks(
                ["101"], uow, user_id="test-user"
            )

        assert result == {}
        assert metrics.failed == 1
        track_repo.save_track.assert_not_awaited()
        recorder.remember_no_match.assert_awaited_once()
        sides = recorder.remember_no_match.await_args.args[0]
        assert [s.identifier for s in sides] == ["101"]
        assert recorder.remember_no_match.await_args.kwargs["connector_name"] == "apple"

    async def test_absent_id_backs_off(self):
        """(e) Id missing from the response entirely → same backoff clock."""
        resolver, _ = _make_resolver([])
        uow, _, _, recorder = _make_uow()

        with patch(STOREFRONT_PATCH, AsyncMock(return_value="us")):
            result, metrics = await resolver.resolve_to_canonical_tracks(
                ["gone404"], uow, user_id="test-user"
            )

        assert result == {}
        assert metrics.failed == 1
        recorder.remember_no_match.assert_awaited_once()
        sides = recorder.remember_no_match.await_args.args[0]
        assert [s.identifier for s in sides] == ["gone404"]

    async def test_failed_chunk_ids_get_no_backoff(self):
        """An id in a failed chunk is unanswered, not absent — recording a
        no-match backoff would delay its retry for our own outage."""
        resolver, _ = _make_resolver([], failed_values=["101"])
        uow, track_repo, _, recorder = _make_uow()

        with patch(STOREFRONT_PATCH, AsyncMock(return_value="us")):
            result, metrics = await resolver.resolve_to_canonical_tracks(
                ["101"], uow, user_id="test-user"
            )

        assert result == {}
        assert metrics.failed == 1
        track_repo.save_track.assert_not_awaited()
        recorder.remember_no_match.assert_not_awaited()

    async def test_no_storefront_fails_batch_without_backoff(self):
        """Storefront unavailable is our problem, not the ids' — no backoff."""
        resolver, client = _make_resolver([])
        uow, _, _, recorder = _make_uow()

        with patch(STOREFRONT_PATCH, AsyncMock(return_value=None)):
            result, metrics = await resolver.resolve_to_canonical_tracks(
                ["101"], uow, user_id="test-user"
            )

        assert result == {}
        assert metrics.failed == 1
        client.get_songs_by_ids.assert_not_awaited()
        recorder.remember_no_match.assert_not_awaited()


class TestCatalogIdDivergence:
    async def test_playparams_divergence_creates_dual_mapping(self):
        """(f) catalogId != id → primary on successor, stale-id secondary."""
        song = make_apple_song(
            song_id="old101", isrc="USUM72309818", catalog_id="new202"
        )
        resolver, _ = _make_resolver([song])
        uow, track_repo, connector_repo, recorder = _make_uow()

        with patch(STOREFRONT_PATCH, AsyncMock(return_value="us")):
            result, _ = await resolver.resolve_to_canonical_tracks(
                ["old101"], uow, user_id="test-user"
            )

        assert "old101" in result
        # The canonical carries the successor id as its apple identifier.
        saved = track_repo.save_track.await_args.args[0]
        assert saved.connector_track_identifiers["apple"] == "new202"

        specs = _mapping_specs(connector_repo)
        assert len(specs) == 2
        primary = next(s for s in specs if s.primary)
        secondary = next(s for s in specs if not s.primary)
        assert primary.connector_id == "new202"
        assert primary.match_method == MatchMethod.DIRECT_IMPORT
        assert secondary.connector_id == "old101"
        assert secondary.match_method == MatchMethod.DIRECT_IMPORT_STALE_ID

        # The substitution is recorded against the requested id.
        recorded = [
            d
            for c in recorder.record.await_args_list
            for d in c.args[0]
            if d.event_type == "substituted"
        ]
        assert len(recorded) == 1
        event = recorded[0]
        assert event.connector_name == "apple"
        assert event.payload["requested_id"] == "old101"
        assert event.payload["returned_id"] == "new202"
        assert event.payload["detection"] == "playparams_catalog_id"

    async def test_matching_catalog_id_is_not_a_substitution(self):
        """catalogId == id → single primary mapping, no substituted event."""
        song = make_apple_song(song_id="101", isrc="USUM72309818", catalog_id="101")
        resolver, _ = _make_resolver([song])
        uow, _, connector_repo, recorder = _make_uow()

        with patch(STOREFRONT_PATCH, AsyncMock(return_value="us")):
            result, _ = await resolver.resolve_to_canonical_tracks(
                ["101"], uow, user_id="test-user"
            )

        assert "101" in result
        specs = _mapping_specs(connector_repo)
        assert [s.connector_id for s in specs] == ["101"]
        recorded = [
            d
            for c in recorder.record.await_args_list
            for d in c.args[0]
            if d.event_type == "substituted"
        ]
        assert recorded == []


class TestBackoffSuppression:
    async def test_backoff_suppressed_ids_are_not_fetched(self):
        """(g) Ids inside their backoff window never reach the API."""
        resolver, client = _make_resolver([])
        uow, _, _, recorder = _make_uow()
        recorder.backoff_suppressed.return_value = frozenset({"101"})

        with patch(STOREFRONT_PATCH, AsyncMock(return_value="us")):
            result, metrics = await resolver.resolve_to_canonical_tracks(
                ["101"], uow, user_id="test-user"
            )

        assert result == {}
        assert metrics.suppressed == 1
        client.get_songs_by_ids.assert_not_awaited()
