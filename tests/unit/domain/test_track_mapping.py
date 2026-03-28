"""Tests for TrackMapping domain entity — origin field defaults."""

from uuid import uuid7

from src.domain.entities.track_mapping import TrackMapping


class TestTrackMappingOrigin:
    def test_default_origin_is_automatic(self):
        mapping = TrackMapping(
            track_id=uuid7(), connector_track_id=uuid7(), connector_name="spotify"
        )
        assert mapping.origin == "automatic"

    def test_origin_can_be_set_to_manual_override(self):
        mapping = TrackMapping(
            track_id=uuid7(),
            connector_track_id=uuid7(),
            connector_name="spotify",
            origin="manual_override",
        )
        assert mapping.origin == "manual_override"
