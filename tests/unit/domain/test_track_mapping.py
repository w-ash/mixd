"""Tests for TrackMapping domain entity — origin and supersession fields."""

from datetime import UTC, datetime
from uuid import uuid7

from src.domain.entities.track_mapping import TrackMapping
from tests.fixtures import TEST_USER_ID


class TestTrackMappingOrigin:
    def test_default_origin_is_automatic(self):
        mapping = TrackMapping(
            match_method="direct",
            track_id=uuid7(),
            connector_track_id=uuid7(),
            connector_name="spotify",
            user_id=TEST_USER_ID,
        )
        assert mapping.origin == "automatic"

    def test_origin_can_be_set_to_manual_override(self):
        mapping = TrackMapping(
            match_method="direct",
            track_id=uuid7(),
            connector_track_id=uuid7(),
            connector_name="spotify",
            origin="manual_override",
            user_id=TEST_USER_ID,
        )
        assert mapping.origin == "manual_override"


class TestTrackMappingSupersession:
    def test_defaults_are_live_and_unscheduled(self):
        # A freshly constructed mapping is live (no supersession recorded) and
        # carries no pending re-verification.
        mapping = TrackMapping(
            match_method="direct",
            track_id=uuid7(),
            connector_track_id=uuid7(),
            connector_name="spotify",
            user_id=TEST_USER_ID,
        )
        assert mapping.superseded_by_id is None
        assert mapping.superseded_at is None
        assert mapping.supersession_reason is None
        assert mapping.supersession_scope is None
        assert mapping.next_verify_at is None

    def test_supersession_fields_round_trip(self):
        successor_id = uuid7()
        superseded_at = datetime.now(UTC)
        mapping = TrackMapping(
            match_method="direct",
            track_id=uuid7(),
            connector_track_id=uuid7(),
            connector_name="spotify",
            superseded_by_id=successor_id,
            superseded_at=superseded_at,
            supersession_reason="rematch",
            supersession_scope="market:GB",
            user_id=TEST_USER_ID,
        )
        assert mapping.superseded_by_id == successor_id
        assert mapping.superseded_at == superseded_at
        assert mapping.supersession_reason == "rematch"
        assert mapping.supersession_scope == "market:GB"

    def test_bare_retirement_has_no_successor(self):
        # id_dead with nothing to relink to: superseded_at/reason are set but
        # superseded_by_id legitimately stays None.
        mapping = TrackMapping(
            match_method="direct",
            track_id=uuid7(),
            connector_track_id=uuid7(),
            connector_name="spotify",
            superseded_at=datetime.now(UTC),
            supersession_reason="id_dead",
            user_id=TEST_USER_ID,
        )
        assert mapping.superseded_by_id is None
        assert mapping.supersession_reason == "id_dead"
