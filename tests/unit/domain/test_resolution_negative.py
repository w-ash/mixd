"""Identity tests for the ResolutionNegative domain entity."""

from src.domain.entities.resolution_negative import ResolutionNegative
from tests.fixtures import TEST_USER_ID


class TestResolutionNegativeConstruction:
    def test_each_negative_gets_a_distinct_id(self):
        assert (
            ResolutionNegative(user_id=TEST_USER_ID).id
            != ResolutionNegative(user_id=TEST_USER_ID).id
        )
