"""Tests for TidalInwardResolver — conservative ISRC-only track minting.

Validates the resolver's outcomes for an answered track (ISRC reuse, suspect
collision deferral, plain creation, no-ISRC refusal), the ``replacement``
successor path (primary on the successor id, stale secondary on the requested
id, ``substituted`` event through the shared seam — Tidal is the first
``SuccessorHook`` implementor), backoff bookkeeping for unresolvable ids,
and backoff suppression inherited from the base class.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config.constants import MatchMethod
from src.domain.exceptions import TidalAuthRequiredError
from src.domain.repositories.connector import ConnectorMappingSpec
from src.infrastructure.connectors._shared.successor_resolution import SuccessorHook
from src.infrastructure.connectors.tidal.client import TIDAL_COUNTRY_CODE
from src.infrastructure.connectors.tidal.inward_resolver import TidalInwardResolver
from tests.fixtures import (
    attach_resolution_recorder,
    make_tidal_track_document,
    make_track,
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


def _make_resolver(documents_by_id=None):
    """Resolver over a mocked client answering ``get_track`` per id."""
    client = AsyncMock()
    documents_by_id = documents_by_id or {}

    async def _get_track(track_id: str, country_code: str):
        assert country_code == TIDAL_COUNTRY_CODE
        return documents_by_id.get(track_id)

    client.get_track.side_effect = _get_track
    return TidalInwardResolver(client=client), client


def _mapping_specs(connector_repo) -> list[ConnectorMappingSpec]:
    return [
        spec
        for c in connector_repo.map_tracks_to_connectors.call_args_list
        for spec in c.args[0]
    ]


def _substituted_events(recorder):
    return [
        d
        for c in recorder.record.await_args_list
        for d in c.args[0]
        if d.event_type == "substituted"
    ]


class TestConnectorContract:
    def test_connector_name_is_tidal(self):
        resolver, _ = _make_resolver()
        assert resolver.connector_name == "tidal"

    def test_normalize_id_strips(self):
        resolver, _ = _make_resolver()
        assert resolver._normalize_id("  12345 ") == "12345"

    def test_resolver_satisfies_successor_hook_protocol(self):
        resolver, _ = _make_resolver()
        assert isinstance(resolver, SuccessorHook)


class TestIsrcReuse:
    async def test_isrc_hit_existing_track_maps_isrc_match(self):
        """An existing canonical holding the ISRC is reused at 95."""
        existing = make_track(7, title="Held Song")
        doc = make_tidal_track_document(track_id="101", isrc="USUM72309818")
        resolver, _ = _make_resolver({"101": doc})
        uow, track_repo, connector_repo, _ = _make_uow()
        track_repo.find_tracks_by_isrcs.return_value = {"USUM72309818": existing}

        result, _ = await resolver.resolve_to_canonical_tracks(
            ["101"], uow, user_id="test-user"
        )

        assert result["101"].id == existing.id
        track_repo.save_track.assert_not_awaited()
        specs = _mapping_specs(connector_repo)
        assert len(specs) == 1
        spec = specs[0]
        assert spec.connector == "tidal"
        assert spec.connector_id == "101"
        assert spec.match_method == MatchMethod.ISRC_MATCH
        assert spec.confidence == MatchMethod.ISRC_MATCH_CONFIDENCE
        assert spec.primary is True

    async def test_suspect_duration_queues_review_and_withholds_isrc(self):
        """>10s duration disagreement → review queued, ISRC withheld."""
        # PT4M20S = 260s; the owner's 200s differs by 60s — suspect.
        doc = make_tidal_track_document(
            track_id="101", isrc="USUM72309818", duration="PT4M20S"
        )
        resolver, _ = _make_resolver({"101": doc})
        uow, track_repo, connector_repo, _ = _make_uow()
        owner = make_track(7, duration_ms=200_000)
        track_repo.find_tracks_by_isrcs.return_value = {"USUM72309818": owner}

        result, _ = await resolver.resolve_to_canonical_tracks(
            ["101"], uow, user_id="test-user"
        )

        assert "101" in result
        # A new canonical was minted, without the contested ISRC.
        track_repo.save_track.assert_awaited_once()
        saved = track_repo.save_track.await_args.args[0]
        assert saved.isrc is None
        connector_repo.queue_isrc_collision_reviews.assert_awaited_once()
        collisions, service = (
            connector_repo.queue_isrc_collision_reviews.await_args.args[:2]
        )
        assert service == "tidal"
        assert collisions[0].owner.id == owner.id
        assert collisions[0].connector_id == "101"
        specs = _mapping_specs(connector_repo)
        assert specs[0].match_method == MatchMethod.DIRECT_IMPORT

    async def test_isrc_without_holder_creates_direct_import(self):
        """Nobody holds the ISRC → new canonical from Tidal metadata."""
        doc = make_tidal_track_document(track_id="101", isrc="USUM72309818")
        resolver, _ = _make_resolver({"101": doc})
        uow, track_repo, connector_repo, _ = _make_uow()

        result, metrics = await resolver.resolve_to_canonical_tracks(
            ["101"], uow, user_id="test-user"
        )

        assert "101" in result
        assert metrics.created == 1
        saved = track_repo.save_track.await_args.args[0]
        assert saved.isrc == "USUM72309818"
        assert saved.title == "Test Song"
        assert [a.name for a in saved.artists] == ["Test Artist"]
        assert saved.duration_ms == 200_000
        assert saved.user_id == "test-user"
        assert saved.connector_track_identifiers["tidal"] == "101"
        spec = _mapping_specs(connector_repo)[0]
        assert spec.match_method == MatchMethod.DIRECT_IMPORT
        assert spec.confidence == 100
        assert spec.primary is True


class TestUnresolvable:
    async def test_track_without_isrc_mints_nothing_and_backs_off(self):
        """Present but ISRC-less → no canonical, no-match backoff."""
        doc = make_tidal_track_document(track_id="101", isrc=None)
        resolver, _ = _make_resolver({"101": doc})
        uow, track_repo, _, recorder = _make_uow()

        result, metrics = await resolver.resolve_to_canonical_tracks(
            ["101"], uow, user_id="test-user"
        )

        assert result == {}
        assert metrics.failed == 1
        track_repo.save_track.assert_not_awaited()
        recorder.remember_no_match.assert_awaited_once()
        sides = recorder.remember_no_match.await_args.args[0]
        assert [s.identifier for s in sides] == ["101"]
        assert sides[0].title == "Test Song"
        assert recorder.remember_no_match.await_args.kwargs["connector_name"] == "tidal"

    async def test_dead_id_without_replacement_backs_off(self):
        """404 with no successor pointer → same backoff clock, unresolved."""
        resolver, _ = _make_resolver({})
        uow, _, _, recorder = _make_uow()

        result, metrics = await resolver.resolve_to_canonical_tracks(
            ["gone404"], uow, user_id="test-user"
        )

        assert result == {}
        assert metrics.failed == 1
        recorder.remember_no_match.assert_awaited_once()
        sides = recorder.remember_no_match.await_args.args[0]
        assert [s.identifier for s in sides] == ["gone404"]


class TestAuthFailure:
    async def test_dead_grant_propagates_bare_not_as_an_exception_group(self):
        """The fan-out must not hide the typed error inside an ExceptionGroup.

        The 409 handler and the CLI's reconnect prompt are both keyed on the
        exception type, so a grouped error reads as an unhandled 500.
        """
        client = AsyncMock()

        async def _get_track(track_id: str, country_code: str):
            raise TidalAuthRequiredError("reconnect Tidal")

        client.get_track.side_effect = _get_track
        resolver = TidalInwardResolver(client=client)
        uow, _, _, _ = _make_uow()

        with pytest.raises(TidalAuthRequiredError):
            _ = await resolver.resolve_to_canonical_tracks(
                ["101", "102", "103"], uow, user_id="test-user"
            )


class TestReplacementSuccessor:
    async def test_dead_id_with_replacement_resolves_the_successor(self):
        """Successor primary + stale secondary + substituted event, via the
        shared seam."""
        dead = make_tidal_track_document(
            track_id="old101", isrc=None, replacement_id="new202"
        )
        successor = make_tidal_track_document(track_id="new202", isrc="USUM72309818")
        resolver, _ = _make_resolver({"old101": dead, "new202": successor})
        uow, track_repo, connector_repo, recorder = _make_uow()

        result, metrics = await resolver.resolve_to_canonical_tracks(
            ["old101"], uow, user_id="test-user"
        )

        assert "old101" in result
        assert metrics.created == 1
        # The canonical carries the successor id as its tidal identifier.
        saved = track_repo.save_track.await_args.args[0]
        assert saved.connector_track_identifiers["tidal"] == "new202"

        specs = _mapping_specs(connector_repo)
        assert len(specs) == 2
        primary = next(s for s in specs if s.primary)
        secondary = next(s for s in specs if not s.primary)
        assert primary.connector_id == "new202"
        assert primary.match_method == MatchMethod.DIRECT_IMPORT
        assert secondary.connector_id == "old101"
        assert secondary.match_method == MatchMethod.DIRECT_IMPORT_STALE_ID

        # The substitution rode the shared helper: recorded against the
        # requested id, with the exact three-key payload the seam emits.
        recorder.connector_track_ids.assert_awaited_once_with(
            ["old101"], connector_name="tidal"
        )
        events = _substituted_events(recorder)
        assert len(events) == 1
        event = events[0]
        assert event.connector_name == "tidal"
        assert set(event.payload) == {"requested_id", "returned_id", "detection"}
        assert event.payload["requested_id"] == "old101"
        assert event.payload["returned_id"] == "new202"
        assert event.payload["detection"] == "replacement_pointer"
        assert event.track_id == result["old101"].id

        # No backoff for a substituted id — it resolved.
        recorder.remember_no_match.assert_not_awaited()

    async def test_two_dead_ids_sharing_one_successor_fan_in_to_one_canonical(self):
        """Duplicate-successor fan-in: ONE canonical create, two stale
        secondaries, two substituted events. Two dead ids sharing a
        replacement must never plan two canonical payloads for the same
        current id — that trips ``save_tracks``' duplicate-identity
        caller-bug warning and mints a duplicate canonical."""
        dead_a = make_tidal_track_document(
            track_id="old101", isrc=None, replacement_id="new202"
        )
        dead_b = make_tidal_track_document(
            track_id="old102", isrc=None, replacement_id="new202"
        )
        successor = make_tidal_track_document(track_id="new202", isrc="USUM72309818")
        resolver, _ = _make_resolver({
            "old101": dead_a,
            "old102": dead_b,
            "new202": successor,
        })
        uow, track_repo, connector_repo, recorder = _make_uow()

        result, _metrics = await resolver.resolve_to_canonical_tracks(
            ["old101", "old102"], uow, user_id="test-user"
        )

        assert set(result) == {"old101", "old102"}
        # One create for the shared successor — both requested ids fan in.
        assert track_repo.save_tracks.await_count == 1
        [payloads] = track_repo.save_tracks.await_args.args
        assert len(payloads) == 1
        assert result["old101"] is result["old102"]

        specs = _mapping_specs(connector_repo)
        assert len(specs) == 3
        primaries = [s for s in specs if s.primary]
        assert [s.connector_id for s in primaries] == ["new202"]
        stale_ids = sorted(s.connector_id for s in specs if not s.primary)
        assert stale_ids == ["old101", "old102"]

        events = _substituted_events(recorder)
        assert {e.payload["requested_id"] for e in events} == {"old101", "old102"}
        assert all(e.payload["returned_id"] == "new202" for e in events)
        # Both ids resolved — nothing lands on the backoff clock.
        recorder.remember_no_match.assert_not_awaited()

    async def test_successor_isrc_reuse_maps_isrc_match_with_stale_secondary(self):
        """Successor's ISRC held by an existing canonical → primary reuse on
        the successor id, ISRC stale secondary on the requested id."""
        existing = make_track(7, title="Held Song")
        dead = make_tidal_track_document(
            track_id="old101", isrc=None, replacement_id="new202"
        )
        successor = make_tidal_track_document(track_id="new202", isrc="USUM72309818")
        resolver, _ = _make_resolver({"old101": dead, "new202": successor})
        uow, track_repo, connector_repo, recorder = _make_uow()
        track_repo.find_tracks_by_isrcs.return_value = {"USUM72309818": existing}

        result, _ = await resolver.resolve_to_canonical_tracks(
            ["old101"], uow, user_id="test-user"
        )

        assert result["old101"].id == existing.id
        track_repo.save_track.assert_not_awaited()
        specs = _mapping_specs(connector_repo)
        primary = next(s for s in specs if s.primary)
        secondary = next(s for s in specs if not s.primary)
        assert primary.connector_id == "new202"
        assert primary.match_method == MatchMethod.ISRC_MATCH
        assert secondary.connector_id == "old101"
        assert secondary.match_method == MatchMethod.ISRC_MATCH_STALE_ID
        assert len(_substituted_events(recorder)) == 1

    async def test_successor_without_isrc_backs_off_the_requested_id(self):
        """A successor the conservative contract cannot mint → no event, no
        mapping, requested id on the backoff clock."""
        dead = make_tidal_track_document(
            track_id="old101", isrc=None, replacement_id="new202"
        )
        successor = make_tidal_track_document(track_id="new202", isrc=None)
        resolver, _ = _make_resolver({"old101": dead, "new202": successor})
        uow, track_repo, _, recorder = _make_uow()

        result, metrics = await resolver.resolve_to_canonical_tracks(
            ["old101"], uow, user_id="test-user"
        )

        assert result == {}
        assert metrics.failed == 1
        track_repo.save_track.assert_not_awaited()
        assert _substituted_events(recorder) == []
        sides = recorder.remember_no_match.await_args.args[0]
        assert [s.identifier for s in sides] == ["old101"]

    async def test_resolve_successors_consults_the_replacement_pointer(self):
        """The SuccessorHook consult: assertions only for ids Tidal asserts a
        successor for; misses absent from the mapping."""
        dead = make_tidal_track_document(
            track_id="old101", isrc=None, replacement_id="new202"
        )
        resolver, _ = _make_resolver({"old101": dead})

        assertions = await resolver.resolve_successors(["old101", "gone404"])

        assert set(assertions) == {"old101"}
        assertion = assertions["old101"]
        assert assertion.requested_id == "old101"
        assert assertion.returned_id == "new202"
        assert assertion.detection == "replacement_pointer"


class TestBackoffSuppression:
    async def test_backoff_suppressed_ids_are_not_fetched(self):
        """Ids inside their backoff window never reach the API."""
        resolver, client = _make_resolver()
        uow, _, _, recorder = _make_uow()
        recorder.backoff_suppressed.return_value = frozenset({"101"})

        result, metrics = await resolver.resolve_to_canonical_tracks(
            ["101"], uow, user_id="test-user"
        )

        assert result == {}
        assert metrics.suppressed == 1
        client.get_track.assert_not_awaited()
