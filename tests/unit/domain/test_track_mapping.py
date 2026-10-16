"""Tests for the TrackMapping domain entity's persisted default origin."""

from uuid import uuid7

from src.domain.entities.track_mapping import TrackMapping
from tests.fixtures import TEST_USER_ID


class TestTrackMappingOrigin:
    def test_default_origin_is_automatic(self):
        # Persisted as-is: a mapping written without an explicit origin must
        # stay re-matchable, never read as a user's manual override.
        mapping = TrackMapping(
            match_method="direct",
            track_id=uuid7(),
            connector_track_id=uuid7(),
            connector_name="spotify",
            user_id=TEST_USER_ID,
        )
        assert mapping.origin == "automatic"
