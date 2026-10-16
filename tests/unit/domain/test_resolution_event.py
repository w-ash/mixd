"""Identity and default-isolation tests for the ResolutionEvent domain entity."""

from src.domain.entities.resolution_event import ResolutionEvent
from tests.fixtures import TEST_USER_ID


class TestResolutionEventConstruction:
    def test_each_event_gets_a_distinct_id(self):
        assert (
            ResolutionEvent(user_id=TEST_USER_ID).id
            != ResolutionEvent(user_id=TEST_USER_ID).id
        )

    def test_payload_default_is_not_shared_between_instances(self):
        # attrs `factory=dict` must produce a fresh dict per instance — a bare
        # `= {}` default would alias every event's payload to one object.
        first = ResolutionEvent(user_id=TEST_USER_ID)
        second = ResolutionEvent(user_id=TEST_USER_ID)
        assert first.payload is not second.payload
