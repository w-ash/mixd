"""Tests for enrichment dependency warnings in metric transforms.

Verifies that sort_by_external_metrics and filter_by_metric_range emit
warnings when the metrics dict is empty but the tracklist has tracks.
"""

import re

import structlog

from src.application.metadata_transforms.metric_transforms import (
    filter_by_metric_range,
    sort_by_external_metrics,
)
from src.domain.entities.track import TrackList
from tests.fixtures import make_track


def _capture_warnings(func):
    """Helper to capture structlog warning messages during a function call."""
    with structlog.testing.capture_logs() as captured:
        func()
    return [e["event"] for e in captured if e.get("log_level") == "warning"]


class TestSortByExternalMetricsWarning:
    def test_warns_on_empty_metrics(self):
        """Sort emits a warning when no metric data is available."""
        tracklist = TrackList(tracks=[make_track(id=1), make_track(id=2)])

        warnings = _capture_warnings(
            lambda: sort_by_external_metrics(
                "lastfm_user_playcount", tracklist=tracklist
            )
        )

        assert any(
            re.search(r"Sort by.*lastfm_user_playcount.*no metric data", msg)
            for msg in warnings
        )

    def test_no_warning_when_metrics_present(self):
        """No warning when metrics exist."""
        tracklist = TrackList(
            tracks=[make_track(id=1)],
            metadata={"metrics": {"lastfm_user_playcount": {1: 42}}},
        )

        warnings = _capture_warnings(
            lambda: sort_by_external_metrics(
                "lastfm_user_playcount", tracklist=tracklist
            )
        )

        assert not any("no metric data" in msg for msg in warnings)

    def test_no_warning_on_empty_tracklist(self):
        """No warning when tracklist is empty — nothing to sort."""
        warnings = _capture_warnings(
            lambda: sort_by_external_metrics(
                "lastfm_user_playcount", tracklist=TrackList()
            )
        )
        assert not any("no metric data" in msg for msg in warnings)


class TestFilterByMetricRangeWarning:
    def test_warns_on_empty_metrics(self):
        """Filter emits a warning when no metric data is available."""
        tracklist = TrackList(tracks=[make_track(id=1)])

        warnings = _capture_warnings(
            lambda: filter_by_metric_range(
                "lastfm_user_playcount", min_value=5, tracklist=tracklist
            )
        )

        assert any(
            re.search(r"Filter by.*lastfm_user_playcount.*no metric data", msg)
            for msg in warnings
        )

    def test_no_warning_when_metrics_present(self):
        """No warning when metrics exist."""
        tracklist = TrackList(
            tracks=[make_track(id=1)],
            metadata={"metrics": {"lastfm_user_playcount": {1: 42}}},
        )

        warnings = _capture_warnings(
            lambda: filter_by_metric_range(
                "lastfm_user_playcount", min_value=5, tracklist=tracklist
            )
        )

        assert not any("no metric data" in msg for msg in warnings)
