"""Tests for OperationResult with summary metrics."""

from src.domain.entities.operations import OperationResult
from src.domain.entities.track import Artist, Track


class TestOperationResultWithSummaryMetrics:
    """Test OperationResult using summary metrics instead of optional fields."""

    def test_operation_result_with_summary_metrics(self):
        """Test creating operation result with summary metrics."""
        result = OperationResult(operation_name="Test Operation")
        result.summary_metrics.add("imported", 97, "Items Imported")

        assert len(result.summary_metrics.metrics) == 1
        assert result.summary_metrics.metrics[0].value == 97
        assert result.summary_metrics.metrics[0].label == "Items Imported"

    def test_operation_result_with_multiple_summary_metrics(self):
        """Test operation result with multiple summary metrics."""
        result = OperationResult(operation_name="Likes Import")
        result.summary_metrics.add("imported", 97, "Likes Imported", significance=1)
        result.summary_metrics.add(
            "already_liked", 53, "Already Liked ✅", significance=2
        )
        result.summary_metrics.add("candidates", 150, "Candidates", significance=3)

        sorted_metrics = result.summary_metrics.sorted()
        assert len(sorted_metrics) == 3
        assert sorted_metrics[0].label == "Likes Imported"
        assert sorted_metrics[1].label == "Already Liked ✅"
        assert sorted_metrics[2].label == "Candidates"

    def test_operation_result_with_percent_summary_metric(self):
        """Test operation result with percentage summary metric."""
        result = OperationResult(operation_name="Export")
        result.summary_metrics.add(
            "success_rate", 94.5, "Success Rate", format="percent"
        )

        metric = result.summary_metrics.metrics[0]
        assert metric.format == "percent"
        assert metric.value == 94.5

    def test_operation_result_preserves_execution_time(self):
        """Test that execution_time remains a direct field."""
        result = OperationResult(
            operation_name="Test",
            execution_time=2.5,
        )

        assert result.execution_time == 2.5

    def test_operation_result_preserves_tracks_list(self):
        """Test that tracks list remains unchanged."""
        track = Track(title="Test", artists=[Artist(name="Artist")])
        result = OperationResult(
            operation_name="Test",
            tracks=[track],
        )

        assert len(result.tracks) == 1
        assert result.tracks[0].title == "Test"

    def test_operation_result_with_metadata(self):
        """Test operation result with metadata dict for batch_id, checkpoint, etc."""
        result = OperationResult(
            operation_name="Play Import",
            metadata={"batch_id": "abc123", "checkpoint": "2024-01-01T00:00:00Z"},
        )

        assert result.metadata["batch_id"] == "abc123"
        assert result.metadata["checkpoint"] == "2024-01-01T00:00:00Z"

    def test_operation_result_preserves_per_track_metrics(self):
        """Test that per-track metrics dict remains for operational data."""
        result = OperationResult(operation_name="Matching")
        result.metrics["similarity"] = {1: 0.95, 2: 0.87}

        assert result.metrics["similarity"][1] == 0.95
        assert result.metrics["similarity"][2] == 0.87
