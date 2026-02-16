"""Tests for UI display utilities with summary metrics."""

from src.domain.entities.operations import OperationResult
from src.domain.entities.track import Artist, Track
from src.interface.cli.ui import _format_metric_value


class TestMetricValueFormatting:
    """Test metric value formatting for different types."""

    def test_format_count_metric(self):
        """Test formatting count metrics as strings."""
        assert _format_metric_value(97, "count") == "97"
        assert _format_metric_value(0, "count") == "0"
        assert _format_metric_value(1000, "count") == "1000"

    def test_format_percent_metric(self):
        """Test formatting percentage metrics."""
        assert _format_metric_value(94.5, "percent") == "94.5%"
        assert _format_metric_value(100.0, "percent") == "100.0%"
        assert _format_metric_value(0.0, "percent") == "0.0%"
        assert _format_metric_value(33.333, "percent") == "33.3%"

    def test_format_duration_metric(self):
        """Test formatting duration metrics in seconds."""
        assert _format_metric_value(2.3, "duration") == "2.3s"
        assert _format_metric_value(0.5, "duration") == "0.5s"
        assert _format_metric_value(120.0, "duration") == "120.0s"


class TestOperationResultDisplay:
    """Test operation result display with summary metrics."""

    def test_result_with_basic_summary_metrics(self):
        """Test operation result contains summary metrics for display."""
        result = OperationResult(operation_name="Test Operation")
        result.summary_metrics.add("imported", 97, "Items Imported", significance=1)
        result.summary_metrics.add("errors", 3, "Errors", significance=2)

        # Verify metrics are accessible
        sorted_metrics = result.summary_metrics.sorted()
        assert len(sorted_metrics) == 2
        assert sorted_metrics[0].label == "Items Imported"
        assert sorted_metrics[0].value == 97
        assert sorted_metrics[1].label == "Errors"

    def test_result_with_percent_summary_metric(self):
        """Test operation result with percentage summary metric."""
        result = OperationResult(operation_name="Export")
        result.summary_metrics.add(
            "success_rate", 94.5, "Success Rate", format="percent", significance=1
        )

        metric = result.summary_metrics.metrics[0]
        assert metric.format == "percent"
        assert _format_metric_value(metric.value, metric.format) == "94.5%"

    def test_result_sorts_metrics_by_significance(self):
        """Test that summary metrics are displayed in correct order."""
        result = OperationResult(operation_name="Complex Operation")
        result.summary_metrics.add("third", 3, "Third Metric", significance=2)
        result.summary_metrics.add("first", 1, "First Metric", significance=0)
        result.summary_metrics.add("second", 2, "Second Metric", significance=1)

        sorted_metrics = result.summary_metrics.sorted()
        assert sorted_metrics[0].label == "First Metric"
        assert sorted_metrics[1].label == "Second Metric"
        assert sorted_metrics[2].label == "Third Metric"

    def test_result_preserves_execution_time_as_field(self):
        """Test that execution_time remains a direct field, not a metric."""
        result = OperationResult(operation_name="Timed Operation", execution_time=2.5)

        assert result.execution_time == 2.5
        # Execution time is NOT in summary metrics
        assert len(result.summary_metrics.metrics) == 0

    def test_result_preserves_tracks_list(self):
        """Test that tracks list remains unchanged."""
        track = Track(id=1, title="Test Track", artists=[Artist(name="Artist")])
        result = OperationResult(operation_name="Track Operation", tracks=[track])

        assert len(result.tracks) == 1
        assert result.tracks[0].title == "Test Track"

    def test_likes_import_result_structure(self):
        """Test likes import result has correct summary metric structure."""
        result = OperationResult(operation_name="Spotify Likes Import")
        result.summary_metrics.add("imported", 97, "Likes Imported", significance=1)
        result.summary_metrics.add(
            "already_liked", 53, "Already Liked ✅", significance=2
        )
        result.summary_metrics.add("candidates", 150, "Candidates", significance=3)
        result.summary_metrics.add(
            "success_rate", 64.7, "Success Rate", format="percent", significance=4
        )

        sorted_metrics = result.summary_metrics.sorted()
        assert len(sorted_metrics) == 4
        assert sorted_metrics[0].label == "Likes Imported"
        assert sorted_metrics[1].label == "Already Liked ✅"
        assert sorted_metrics[2].label == "Candidates"
        assert sorted_metrics[3].label == "Success Rate"
        assert sorted_metrics[3].format == "percent"

    def test_play_import_result_structure(self):
        """Test play import result has correct summary metric structure."""
        result = OperationResult(operation_name="Play Import")
        result.summary_metrics.add("raw_plays", 1000, "Raw Plays Found", significance=0)
        result.summary_metrics.add(
            "imported", 950, "Track Plays Created", significance=1
        )
        result.summary_metrics.add(
            "filtered", 30, "Filtered (Too Short)", significance=2
        )
        result.summary_metrics.add(
            "duplicates", 20, "Filtered (Duplicates)", significance=3
        )

        sorted_metrics = result.summary_metrics.sorted()
        assert sorted_metrics[0].label == "Raw Plays Found"
        assert sorted_metrics[1].label == "Track Plays Created"
        assert sorted_metrics[2].label == "Filtered (Too Short)"
        assert sorted_metrics[3].label == "Filtered (Duplicates)"
