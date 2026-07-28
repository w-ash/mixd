"""Construction smoke tests for the ResolutionNegative domain entity."""

from datetime import UTC, datetime, timedelta
from uuid import uuid7

from src.domain.entities.resolution_negative import ResolutionNegative


class TestResolutionNegativeConstruction:
    def test_defaults(self):
        negative = ResolutionNegative()
        assert negative.user_id == "default"
        assert negative.kind == "no_match"
        assert negative.connector_name == ""
        assert negative.connector_track_id is None
        assert negative.candidate_track_id is None
        assert negative.matcher_version == ""
        assert negative.content_digest is None
        assert negative.check_again is None
        assert negative.last_checked_at is None
        assert negative.unrejected_at is None

    def test_no_match_has_no_candidate(self):
        connector_track_id = uuid7()
        check_again = datetime.now(UTC) + timedelta(days=1)
        negative = ResolutionNegative(
            kind="no_match",
            connector_name="spotify",
            connector_track_id=connector_track_id,
            matcher_version="abc123def456",
            check_again=check_again,
            last_checked_at=datetime.now(UTC),
        )
        assert negative.kind == "no_match"
        assert negative.candidate_track_id is None
        assert negative.check_again == check_again

    def test_rejected_pair_carries_both_sides_and_a_digest(self):
        connector_track_id = uuid7()
        candidate_track_id = uuid7()
        negative = ResolutionNegative(
            kind="rejected_pair",
            connector_name="spotify",
            connector_track_id=connector_track_id,
            candidate_track_id=candidate_track_id,
            matcher_version="abc123def456",
            content_digest="deadbeef",
        )
        assert negative.kind == "rejected_pair"
        assert negative.connector_track_id == connector_track_id
        assert negative.candidate_track_id == candidate_track_id
        assert negative.content_digest == "deadbeef"
        assert negative.unrejected_at is None

    def test_unrejected_pair_records_the_timestamp(self):
        unrejected_at = datetime.now(UTC)
        negative = ResolutionNegative(
            kind="rejected_pair",
            candidate_track_id=uuid7(),
            unrejected_at=unrejected_at,
        )
        assert negative.unrejected_at == unrejected_at

    def test_each_negative_gets_a_distinct_id(self):
        assert ResolutionNegative().id != ResolutionNegative().id
