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


class TestOperationResultFailureSignal:
    """The shared failure predicate read by the scheduler, web SSE seam, and CLI."""

    def test_clean_result_is_not_a_failure(self):
        result = OperationResult(operation_name="Import")
        result.summary_metrics.add("track_plays", 42, "Plays Imported")

        assert result.is_failure is False

    def test_positive_errors_metric_is_a_failure(self):
        result = OperationResult(operation_name="Import")
        result.summary_metrics.add("errors", 1, "Errors", significance=1)

        assert result.is_failure is True

    def test_zero_errors_metric_is_not_a_failure(self):
        result = OperationResult(operation_name="Import")
        result.summary_metrics.add("errors", 0, "Errors", significance=1)

        assert result.is_failure is False

    def test_error_metadata_key_is_a_failure(self):
        result = OperationResult(operation_name="Import")
        result.metadata["error"] = "Last.fm timed out"

        assert result.is_failure is True


class TestPartialFailure:
    """is_partial_failure separates "finished with casualties" from "failed"."""

    def test_errors_alongside_successes_is_partial(self):
        result = OperationResult(operation_name="Import")
        result.summary_metrics.add("track_plays", 98, "Track Plays Created")
        result.summary_metrics.add("errors", 2, "Errors", significance=1)

        assert result.is_partial_failure is True

    def test_partial_is_still_a_failure_for_the_scheduler(self):
        # The scheduler's health signal reads is_failure; partial refines the
        # audit row only and must never soften that signal.
        result = OperationResult(operation_name="Import")
        result.summary_metrics.add("track_plays", 98, "Track Plays Created")
        result.summary_metrics.add("errors", 2, "Errors", significance=1)

        assert result.is_failure is True

    def test_errors_without_successes_is_not_partial(self):
        result = OperationResult(operation_name="Import")
        result.summary_metrics.add("errors", 5, "Errors", significance=1)

        assert result.is_partial_failure is False

    def test_denominator_metrics_are_not_successes(self):
        # raw_plays/candidates/total count attempts, not completions — a run
        # where nothing landed must not read as partial.
        result = OperationResult(operation_name="Import")
        result.summary_metrics.add("raw_plays", 100, "Raw Plays Found")
        result.summary_metrics.add("candidates", 100, "Candidates")
        result.summary_metrics.add("total", 100, "Total Plays")
        result.summary_metrics.add("errors", 100, "Errors", significance=1)

        assert result.is_partial_failure is False

    def test_clean_result_is_not_partial(self):
        result = OperationResult(operation_name="Import")
        result.summary_metrics.add("track_plays", 42, "Track Plays Created")

        assert result.is_partial_failure is False

    def test_error_metadata_with_successes_is_partial(self):
        result = OperationResult(operation_name="Likes Export")
        result.summary_metrics.add("exported", 7, "Exported")
        result.metadata["error"] = "3 of 10 likes failed to export"

        assert result.is_partial_failure is True

    def test_zero_valued_success_metric_does_not_count(self):
        result = OperationResult(operation_name="Import")
        result.summary_metrics.add("track_plays", 0, "Track Plays Created")
        result.summary_metrics.add("errors", 3, "Errors", significance=1)

        assert result.is_partial_failure is False


class TestResolutionFailures:
    """Per-item failure detail the SSE seam turns into individual run issues."""

    def test_recorded_failures_returned(self):
        result = OperationResult(operation_name="Import")
        result.metadata["resolution_failures"] = [
            {"track": "A - B", "spotify_id": "x", "reason": "track_resolution_failed"}
        ]

        assert result.resolution_failures == [
            {"track": "A - B", "spotify_id": "x", "reason": "track_resolution_failed"}
        ]

    def test_absent_metadata_yields_empty_list(self):
        assert OperationResult(operation_name="Import").resolution_failures == []

    def test_non_mapping_entries_filtered(self):
        result = OperationResult(operation_name="Import")
        result.metadata["resolution_failures"] = ["not a dict", {"track": "A - B"}]

        assert result.resolution_failures == [{"track": "A - B"}]

    def test_truncated_count_read(self):
        result = OperationResult(operation_name="Import")
        result.metadata["resolution_failures_truncated"] = 12

        assert result.resolution_failures_truncated == 12

    def test_truncated_defaults_to_zero(self):
        assert (
            OperationResult(operation_name="Import").resolution_failures_truncated == 0
        )


class TestFailureMessage:
    """failure_message surfaces the soft-failure reason to_counts() drops."""

    def test_error_string_returned(self):
        result = OperationResult(operation_name="Import")
        result.metadata["error"] = "Last.fm timed out"

        assert result.failure_message == "Last.fm timed out"

    def test_errors_list_joined(self):
        result = OperationResult(operation_name="Import")
        result.metadata["errors"] = ["boom one", "boom two"]

        assert result.failure_message == "boom one; boom two"

    def test_error_string_wins_over_errors_list(self):
        result = OperationResult(operation_name="Import")
        result.metadata["error"] = "primary"
        result.metadata["errors"] = ["secondary"]

        assert result.failure_message == "primary"

    def test_non_string_entries_filtered(self):
        result = OperationResult(operation_name="Import")
        result.metadata["errors"] = [1, "real message", None]

        assert result.failure_message == "real message"

    def test_no_message_returns_none(self):
        result = OperationResult(operation_name="Import")
        result.summary_metrics.add("errors", 1, "Errors", significance=1)

        assert result.failure_message is None


class TestOperationResultToCounts:
    """to_counts() flattens summary metrics for the audit row and SSE terminal event."""

    def test_flattens_metric_names_to_values(self):
        result = OperationResult(operation_name="Likes Import")
        result.summary_metrics.add("imported", 97, "Likes Imported", significance=1)
        result.summary_metrics.add("already_liked", 53, "Already Liked", significance=2)

        assert result.to_counts() == {"imported": 97, "already_liked": 53}

    def test_empty_metrics_produce_empty_counts(self):
        assert OperationResult(operation_name="No-op").to_counts() == {}
