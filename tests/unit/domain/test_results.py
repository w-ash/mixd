"""Tests for domain result data types and factory functions."""

from datetime import UTC, datetime

from src.domain.entities.operations import ConnectorTrackPlay
from src.domain.results import ImportResultData, create_import_result


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
        """Factory should populate summary metrics from import data."""
        import_data = ImportResultData(
            raw_data_count=10,
            imported_count=8,
            duplicate_count=2,
            batch_id="batch-123",
        )

        result = create_import_result("test_import", import_data)

        assert result.metadata["batch_id"] == "batch-123"
        assert result.summary_metrics.get("raw_plays") == 10
