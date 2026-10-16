"""Tests for domain result data types and factory functions."""

from datetime import UTC, datetime

from src.domain.entities.operations import ConnectorTrackPlay
from src.domain.results import ImportResultData, create_import_result
from tests.fixtures import TEST_USER_ID


class TestCreateImportResult:
    """Tests for the create_import_result factory function."""

    def test_import_tracks_not_forwarded_to_operation_result(self):
        """ConnectorTrackPlay objects in ImportResultData should NOT flow to OperationResult.tracks.

        OperationResult.tracks expects list[Track], but import results contain
        ConnectorTrackPlay (different entity). The factory must not forward them.
        """
        plays = [
            ConnectorTrackPlay(
                artist_name="Artist",
                track_name="Track",
                played_at=datetime.now(UTC),
                service="spotify",
                user_id=TEST_USER_ID,
            ),
        ]
        import_data = ImportResultData(
            raw_data_count=1,
            imported_count=1,
            batch_id="test-batch",
            tracks=plays,
        )

        result = create_import_result("test_import", import_data)

        assert result.tracks == []
        assert result.operation_name == "test_import"

    def test_import_metrics_populated(self):
        """Non-zero counts become metrics; success rate is imported / attempted.

        Attempted = imported + duplicates + errors, so 8 of 10 is 80%. Zero
        counts (filtered, new/updated tracks, errors) add no metric at all.
        """
        import_data = ImportResultData(
            raw_data_count=10,
            imported_count=8,
            duplicate_count=2,
            batch_id="batch-123",
            checkpoint_timestamp=datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
        )

        result = create_import_result("test_import", import_data)

        assert {m.name: m.value for m in result.summary_metrics.metrics} == {
            "raw_plays": 10,
            "imported": 8,
            "duplicates": 2,
            "success_rate": 80.0,
        }
        assert result.summary_metrics.metrics[-1].format == "percent"
        assert result.metadata == {
            "batch_id": "batch-123",
            "checkpoint_timestamp": "2026-01-02T03:04:05+00:00",
        }
