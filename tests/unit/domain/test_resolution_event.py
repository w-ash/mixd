"""Construction smoke tests for the ResolutionEvent domain entity."""

from datetime import UTC, datetime
from uuid import uuid7

from src.domain.entities.resolution_event import ResolutionEvent


class TestResolutionEventConstruction:
    def test_defaults(self):
        event = ResolutionEvent()
        assert event.user_id == "default"
        assert event.event_type == "accepted"
        assert event.matcher_version == ""
        assert event.recorded_at is None
        assert event.decided_at is None
        assert event.evidence_as_of is None
        assert event.run_id is None
        assert event.connector_name is None
        assert event.connector_track_id is None
        assert event.track_id is None
        assert event.resulting_mapping_id is None
        assert event.confidence is None
        assert event.score is None
        assert event.zone is None
        assert event.selection_probability is None
        assert event.payload == {}

    def test_full_construction_round_trips_every_field(self):
        run_id = uuid7()
        connector_track_id = uuid7()
        track_id = uuid7()
        mapping_id = uuid7()
        decided_at = datetime.now(UTC)
        evidence_as_of = datetime.now(UTC)
        event = ResolutionEvent(
            user_id="alice",
            event_type="rejected",
            matcher_version="abc123def456",
            decided_at=decided_at,
            evidence_as_of=evidence_as_of,
            run_id=run_id,
            connector_name="spotify",
            connector_track_id=connector_track_id,
            track_id=track_id,
            resulting_mapping_id=mapping_id,
            confidence=87,
            score=12.5,
            zone="auto_accept",
            selection_probability=0.5,
            payload={"requested_id": "abc", "market": "GB"},
        )
        assert event.user_id == "alice"
        assert event.event_type == "rejected"
        assert event.matcher_version == "abc123def456"
        # recorded_at is DB-assigned; the domain entity itself never sets it.
        assert event.recorded_at is None
        assert event.decided_at == decided_at
        assert event.evidence_as_of == evidence_as_of
        assert event.run_id == run_id
        assert event.connector_name == "spotify"
        assert event.connector_track_id == connector_track_id
        assert event.track_id == track_id
        assert event.resulting_mapping_id == mapping_id
        assert event.confidence == 87
        assert event.score == 12.5
        assert event.zone == "auto_accept"
        assert event.selection_probability == 0.5
        assert event.payload == {"requested_id": "abc", "market": "GB"}

    def test_each_event_gets_a_distinct_id(self):
        assert ResolutionEvent().id != ResolutionEvent().id

    def test_payload_default_is_not_shared_between_instances(self):
        # attrs `factory=dict` must produce a fresh dict per instance — a bare
        # `= {}` default would alias every event's payload to one object.
        first = ResolutionEvent()
        second = ResolutionEvent()
        assert first.payload is not second.payload
