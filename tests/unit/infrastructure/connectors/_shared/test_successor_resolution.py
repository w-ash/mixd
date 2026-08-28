"""Tests for the shared platform-asserted successor recording seam.

Characterizes the contract Spotify relinking and Apple equivalents migrated
onto (T8): one batched connector-track lookup, one batched event insert,
events keyed to the *requested* id's connector track, and a payload whose
key-set is exactly {requested_id, returned_id, detection, *extra-keys}.
"""

from unittest.mock import AsyncMock
from uuid import uuid4

from attrs.exceptions import FrozenInstanceError
import pytest

from src.config.constants import MatchMethod
from src.infrastructure.connectors._shared.successor_resolution import (
    SuccessorAssertion,
    record_substitutions,
    stale_id_mapping_spec,
)
from tests.fixtures import make_track


def _recorder(lookup: dict) -> AsyncMock:
    recorder = AsyncMock()
    recorder.connector_track_ids.return_value = lookup
    recorder.record.return_value = len(lookup)
    return recorder


class TestRecordSubstitutions:
    async def test_payload_keys_are_exactly_requested_returned_detection_extra(self):
        ct_id = uuid4()
        track_id = uuid4()
        recorder = _recorder({"old-id": ct_id})
        assertion = SuccessorAssertion(
            requested_id="old-id",
            returned_id="new-id",
            detection="id_mismatch",
            track_id=track_id,
            extra={"market": "US"},
        )

        await record_substitutions(
            recorder,
            connector_name="spotify",
            assertions=[assertion],
            user_id="user-1",
        )

        decisions = recorder.record.call_args.args[0]
        assert len(decisions) == 1
        decision = decisions[0]
        assert decision.event_type == "substituted"
        assert decision.connector_name == "spotify"
        assert decision.connector_track_id == ct_id
        assert decision.track_id == track_id
        assert decision.payload == {
            "requested_id": "old-id",
            "returned_id": "new-id",
            "detection": "id_mismatch",
            "market": "US",
        }
        assert recorder.record.call_args.kwargs == {"user_id": "user-1"}

    async def test_no_extra_yields_three_key_payload(self):
        recorder = _recorder({"a1": uuid4()})
        assertion = SuccessorAssertion(
            requested_id="a1",
            returned_id="a2",
            detection="playparams_catalog_id",
            track_id=uuid4(),
        )

        await record_substitutions(
            recorder,
            connector_name="apple_music",
            assertions=[assertion],
            user_id="user-1",
        )

        payload = recorder.record.call_args.args[0][0].payload
        assert set(payload) == {"requested_id", "returned_id", "detection"}

    async def test_batched_one_lookup_and_one_record_call(self):
        lookup = {"r1": uuid4(), "r2": uuid4(), "r3": uuid4()}
        recorder = _recorder(lookup)
        assertions = [
            SuccessorAssertion(
                requested_id=req,
                returned_id=f"{req}-new",
                detection="replacement_pointer",
                track_id=uuid4(),
            )
            for req in ("r1", "r2", "r3")
        ]

        await record_substitutions(
            recorder,
            connector_name="tidal",
            assertions=assertions,
            user_id="user-1",
        )

        recorder.connector_track_ids.assert_awaited_once_with(
            ["r1", "r2", "r3"], connector_name="tidal"
        )
        recorder.record.assert_awaited_once()
        decisions = recorder.record.call_args.args[0]
        assert [d.connector_track_id for d in decisions] == [
            lookup["r1"],
            lookup["r2"],
            lookup["r3"],
        ]

    async def test_missing_connector_track_id_recorded_as_none(self):
        """A lookup miss does not drop the event — it records with no

        connector track, mirroring the pre-extraction Spotify/Apple behavior
        (``requested_ct.get(...)`` passed straight through).
        """
        recorder = _recorder({})
        assertion = SuccessorAssertion(
            requested_id="unknown",
            returned_id="new",
            detection="id_mismatch",
            track_id=uuid4(),
        )

        await record_substitutions(
            recorder,
            connector_name="spotify",
            assertions=[assertion],
            user_id="user-1",
        )

        decisions = recorder.record.call_args.args[0]
        assert len(decisions) == 1
        assert decisions[0].connector_track_id is None

    async def test_empty_assertions_touch_nothing(self):
        recorder = _recorder({})

        await record_substitutions(
            recorder,
            connector_name="spotify",
            assertions=[],
            user_id="user-1",
        )

        recorder.connector_track_ids.assert_not_awaited()
        recorder.record.assert_not_awaited()


class TestStaleIdMappingSpec:
    def test_produces_non_primary_spec_with_mapped_method(self):
        track = make_track()
        spec = stale_id_mapping_spec(
            track=track,
            connector="spotify",
            requested_id="stale-id",
            primary_method=MatchMethod.DIRECT_IMPORT,
            confidence=100,
        )

        assert spec.track is track
        assert spec.connector == "spotify"
        assert spec.connector_id == "stale-id"
        assert spec.match_method == MatchMethod.DIRECT_IMPORT_STALE_ID
        assert spec.confidence == 100
        assert spec.metadata is None
        assert spec.primary is False

    def test_metadata_passes_through(self):
        spec = stale_id_mapping_spec(
            track=make_track(),
            connector="apple_music",
            requested_id="old",
            primary_method=MatchMethod.DIRECT_IMPORT,
            confidence=90,
            metadata={"source": "equivalents"},
        )

        assert spec.metadata == {"source": "equivalents"}

    def test_isrc_match_maps_to_its_stale_variant(self):
        spec = stale_id_mapping_spec(
            track=make_track(),
            connector="tidal",
            requested_id="old",
            primary_method=MatchMethod.ISRC_MATCH,
            confidence=MatchMethod.ISRC_MATCH_CONFIDENCE,
        )

        assert spec.match_method == MatchMethod.ISRC_MATCH_STALE_ID

    def test_method_without_stale_variant_raises(self):
        # ``STALE_ID_FOR`` is authoritative: a primary method with no stale
        # variant is a caller bug, not a silent pass-through.
        with pytest.raises(KeyError):
            _ = stale_id_mapping_spec(
                track=make_track(),
                connector="spotify",
                requested_id="old",
                primary_method=MatchMethod.CANONICAL_REUSE,
                confidence=100,
            )


class TestSuccessorAssertion:
    def test_assertion_is_immutable(self):
        assertion = SuccessorAssertion(
            requested_id="a", returned_id="b", detection="id_mismatch"
        )
        with pytest.raises(FrozenInstanceError):
            assertion.requested_id = "c"  # type: ignore[misc]

    def test_extra_defaults_empty(self):
        assertion = SuccessorAssertion(
            requested_id="a", returned_id="b", detection="id_mismatch"
        )
        assert dict(assertion.extra) == {}
        assert assertion.track_id is None
