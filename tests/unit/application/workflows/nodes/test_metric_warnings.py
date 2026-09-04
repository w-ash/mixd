"""Tests for the missing-enricher warning on metric transform nodes.

The transforms themselves are pure and degrade gracefully; the node layer warns
when a metric node runs against a tracklist that was never enriched.
"""

import re
from typing import cast

import structlog

from src.application.workflows.nodes.execution_context import NodeContext
from src.application.workflows.nodes.transform_definitions import TRANSFORM_REGISTRY
from src.domain.entities.shared import JsonValue
from src.domain.entities.track import TrackList
from src.domain.transforms.core import Transform
from tests.fixtures import make_track


def _apply(
    category: str, operation: str, cfg: dict[str, JsonValue], tracklist: TrackList
):
    """Build a registry transform and apply it, returning captured warnings."""
    ctx = cast(NodeContext, None)
    transform = cast(
        Transform, TRANSFORM_REGISTRY[category][operation].factory(ctx, cfg)
    )
    with structlog.testing.capture_logs() as captured:
        transform(tracklist)
    return [e["event"] for e in captured if e.get("log_level") == "warning"]


class TestSortByMetricWarning:
    def test_warns_on_empty_metrics(self):
        """Sort emits a warning when no metric data is available."""
        tracklist = TrackList(tracks=[make_track(id=1), make_track(id=2)])

        warnings = _apply(
            "sorter", "by_metric", {"metric_name": "lastfm_user_playcount"}, tracklist
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

        warnings = _apply(
            "sorter", "by_metric", {"metric_name": "lastfm_user_playcount"}, tracklist
        )

        assert not any("no metric data" in msg for msg in warnings)

    def test_no_warning_on_empty_tracklist(self):
        """No warning when tracklist is empty — nothing to sort."""
        warnings = _apply(
            "sorter", "by_metric", {"metric_name": "lastfm_user_playcount"}, TrackList()
        )

        assert not any("no metric data" in msg for msg in warnings)

    def test_no_warning_for_track_attribute_sort(self):
        """Track attributes are not enriched metrics — no warning expected."""
        tracklist = TrackList(tracks=[make_track(id=1)])

        warnings = _apply("sorter", "by_metric", {"metric_name": "title"}, tracklist)

        assert not any("no metric data" in msg for msg in warnings)


class TestFilterByMetricWarning:
    def test_warns_on_empty_metrics(self):
        """Filter emits a warning when no metric data is available."""
        tracklist = TrackList(tracks=[make_track(id=1)])

        warnings = _apply(
            "filter",
            "by_metric",
            {"metric_name": "lastfm_user_playcount", "min_value": 5},
            tracklist,
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

        warnings = _apply(
            "filter",
            "by_metric",
            {"metric_name": "lastfm_user_playcount", "min_value": 5},
            tracklist,
        )

        assert not any("no metric data" in msg for msg in warnings)
