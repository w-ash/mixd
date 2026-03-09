"""Tests for enrichment dependency warnings in metric transforms.

Verifies that sort_by_external_metrics and filter_by_metric_range emit
warnings when the metrics dict is empty but the tracklist has tracks.
"""

import re

from loguru import logger

from src.application.metadata_transforms.metric_transforms import (
    filter_by_metric_range,
    sort_by_external_metrics,
)
from src.domain.entities.track import TrackList
from tests.fixtures import make_track


def _capture_loguru_warnings(func):
    """Helper to capture loguru warning messages during a function call."""
    captured: list[str] = []

    def sink(message):
        if message.record["level"].name == "WARNING":
            captured.append(message.record["message"])

    sink_id = logger.add(sink, level="WARNING", format="{message}")
    try:
        func()
    finally:
        logger.remove(sink_id)
    return captured


class TestSortByExternalMetricsWarning:
    def test_warns_on_empty_metrics(self):
        """Sort emits a warning when no metric data is available."""
        tracklist = TrackList(tracks=[make_track(id=1), make_track(id=2)])

        warnings = _capture_loguru_warnings(
            lambda: sort_by_external_metrics("lastfm_user_playcount", tracklist=tracklist)
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

        warnings = _capture_loguru_warnings(
            lambda: sort_by_external_metrics("lastfm_user_playcount", tracklist=tracklist)
        )

        assert not any("no metric data" in msg for msg in warnings)

    def test_no_warning_on_empty_tracklist(self):
        """No warning when tracklist is empty — nothing to sort."""
        warnings = _capture_loguru_warnings(
            lambda: sort_by_external_metrics("lastfm_user_playcount", tracklist=TrackList())
        )
        assert not any("no metric data" in msg for msg in warnings)


class TestFilterByMetricRangeWarning:
    def test_warns_on_empty_metrics(self):
        """Filter emits a warning when no metric data is available."""
        tracklist = TrackList(tracks=[make_track(id=1)])

        warnings = _capture_loguru_warnings(
            lambda: filter_by_metric_range("lastfm_user_playcount", min_value=5, tracklist=tracklist)
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

        warnings = _capture_loguru_warnings(
            lambda: filter_by_metric_range("lastfm_user_playcount", min_value=5, tracklist=tracklist)
        )

        assert not any("no metric data" in msg for msg in warnings)
