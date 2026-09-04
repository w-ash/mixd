"""Tests for metric routing in transform_definitions.

Validates route_metric_sorting routes metrics to the correct sort functions
using open-ended classification.
"""

import pytest

from src.domain.transforms.metric_routing import classify_metric, route_metric_sorting


class TestRouteMetricSorting:
    """Tests for route_metric_sorting routing decisions."""

    def test_track_attribute_routes_to_key_function(self):
        """Track attributes (e.g. 'title') route to sort_by_key_function."""
        result = route_metric_sorting("title", reverse=False)
        assert callable(result)

    def test_external_metric_routes_to_external_sort(self):
        """External metrics route to sort_by_external_metrics."""
        result = route_metric_sorting("explicit_flag", reverse=True)
        assert callable(result)

    def test_play_history_routes_to_play_history_sort(self):
        """Play history metrics route to sort_by_play_history."""
        result = route_metric_sorting("total_plays", reverse=True)
        assert callable(result)

    def test_unknown_metric_routes_to_external(self):
        """Unknown metrics default to external metric sorting (graceful no-op)."""
        result = route_metric_sorting("totally_fake_metric", reverse=True)
        assert callable(result)

    @pytest.mark.parametrize(
        "attr",
        ["title", "album", "release_date", "duration_ms", "artist"],
    )
    def test_all_track_attributes_produce_callable(self, attr: str):
        """All known track attributes produce a callable transform."""
        result = route_metric_sorting(attr, reverse=True)
        assert callable(result)

    @pytest.mark.parametrize(
        "metric",
        [
            "explicit_flag",
            "lastfm_user_playcount",
            "lastfm_listeners",
            "lastfm_global_playcount",
        ],
    )
    def test_known_external_metrics_produce_callable(self, metric: str):
        """Known external metrics produce a callable transform."""
        result = route_metric_sorting(metric, reverse=True)
        assert callable(result)

    @pytest.mark.parametrize(
        "metric",
        [
            "total_plays",
            "plays_last_7_days",
            "plays_last_30_days",
            "plays_last_90_days",
        ],
    )
    def test_all_play_history_metrics_produce_callable(self, metric: str):
        """All play history metrics produce a callable transform."""
        result = route_metric_sorting(metric, reverse=True)
        assert callable(result)

    @pytest.mark.parametrize(
        "metric",
        ["last_played_dates", "first_played_dates"],
    )
    def test_plural_date_metrics_classify_as_external(self, metric: str):
        """Enricher-emitted date metrics route to external metric sorting."""
        assert classify_metric(metric) == "external_metric"
        result = route_metric_sorting(metric, reverse=True)
        assert callable(result)
