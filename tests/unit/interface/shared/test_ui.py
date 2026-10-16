"""Tests for UI display utilities with summary metrics."""

from src.domain.entities.operations import OperationResult
from src.interface.cli.ui import (
    _format_metric_value,
    console,
    display_operation_result,
)
from tests.fixtures import make_track


class TestMetricValueFormatting:
    """Test metric value formatting for different types."""

    def test_format_count_metric(self):
        """Counts render without a decimal point, even when stored as a float."""
        assert _format_metric_value(97, "count") == "97"
        assert _format_metric_value(0, "count") == "0"
        assert _format_metric_value(1000, "count") == "1000"
        assert _format_metric_value(97.0, "count") == "97"

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


class TestTableRenderingCharacterization:
    """Lock the rendered table output across the renderer split.

    ``display_operation_result`` funnels every table result through
    ``_render_summary_table`` + ``_render_track_details_table``; these
    characterize the summary metrics, the "Track Details" table with its
    dynamic metric columns, and the play-import skip so the split stays
    behavior-identical.
    """

    def test_table_output_has_summary_and_track_details(self):
        track = make_track(title="Creep", artist="Radiohead")
        result = OperationResult(
            operation_name="Enrich",
            tracks=[track],
            metrics={"playcount": {track.id: 12}},
        )
        result.summary_metrics.add("enriched", 1, "Tracks Enriched", significance=1)

        with console.capture() as capture:
            display_operation_result(result, output_format="table")
        out = capture.get()

        assert "Tracks Enriched" in out
        assert "Track Details" in out
        assert "Radiohead" in out
        assert "Creep" in out
        assert "Playcount" in out  # dynamic metric column header

    def test_play_import_operation_skips_track_details(self):
        track = make_track(title="Creep", artist="Radiohead")
        result = OperationResult(operation_name="Spotify Import", tracks=[track])

        with console.capture() as capture:
            display_operation_result(result, output_format="table")
        out = capture.get()

        assert "Track Details" not in out
