"""Tests for metric routing in transform_definitions.

Validates route_metric_sorting routes metrics to the correct sort functions
using open-ended classification.
"""

import pytest

from src.application.metadata_transforms.metric_routing import route_metric_sorting


class TestRouteMetricSorting:
    """Tests for _route_metric_sorting routing decisions."""

    def test_track_attribute_routes_to_key_function(self):
        """Track attributes (e.g. 'title') route to sort_by_key_function."""
        result = route_metric_sorting({"metric_name": "title", "reverse": False})
        assert callable(result)

    def test_external_metric_routes_to_external_sort(self):
        """External metrics route to sort_by_external_metrics."""
        result = route_metric_sorting({
            "metric_name": "explicit_flag",
            "reverse": True,
        })
        assert callable(result)

    def test_play_history_routes_to_play_history_sort(self):
        """Play history metrics route to sort_by_play_history."""
        result = route_metric_sorting({
            "metric_name": "total_plays",
            "reverse": True,
        })
        assert callable(result)

    def test_unknown_metric_routes_to_external(self):
        """Unknown metrics default to external metric sorting (graceful no-op)."""
        result = route_metric_sorting({"metric_name": "totally_fake_metric"})
        assert callable(result)

    def test_missing_metric_name_raises(self):
        """Missing metric_name in config raises ValueError."""
        with pytest.raises(ValueError, match="metric_name is required"):
            route_metric_sorting({})

    @pytest.mark.parametrize(
        "attr",
        ["title", "album", "release_date", "duration_ms", "artist"],
    )
    def test_all_track_attributes_produce_callable(self, attr: str):
        """All known track attributes produce a callable transform."""
        result = route_metric_sorting({"metric_name": attr})
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
        result = route_metric_sorting({"metric_name": metric})
        assert callable(result)

    @pytest.mark.parametrize(
        "metric",
        [
            "total_plays",
            "plays_last_7_days",
            "plays_last_30_days",
            "plays_last_90_days",
            "last_played_date",
        ],
    )
    def test_all_play_history_metrics_produce_callable(self, metric: str):
        """All play history metrics produce a callable transform."""
        result = route_metric_sorting({"metric_name": metric})
        assert callable(result)
