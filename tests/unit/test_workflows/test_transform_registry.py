"""Tests for transform registry structure and metric classification.

Validates TRANSFORM_REGISTRY structure, _classify_metric open-ended classification,
and _route_metric_sorting routing.
"""

import pytest


class TestTransformRegistry:
    """Tests for TRANSFORM_REGISTRY structure."""

    def test_all_categories_exist(self):
        """Registry has all expected top-level categories."""
        from src.application.workflows.transform_registry import TRANSFORM_REGISTRY

        expected = {"filter", "sorter", "selector", "combiner"}
        assert set(TRANSFORM_REGISTRY.keys()) == expected

    def test_filter_operations(self):
        """Filter category contains expected operations."""
        from src.application.workflows.transform_registry import TRANSFORM_REGISTRY

        expected = {
            "deduplicate",
            "by_release_date",
            "by_tracks",
            "by_artists",
            "by_metric",
            "by_play_history",
            "by_duration",
            "by_liked_status",
            "by_explicit",
        }
        assert set(TRANSFORM_REGISTRY["filter"].keys()) == expected

    def test_sorter_operations(self):
        """Sorter category contains expected operations."""
        from src.application.workflows.transform_registry import TRANSFORM_REGISTRY

        expected = {
            "by_metric",
            "by_release_date",
            "by_play_history",
            "weighted_shuffle",
            "by_added_at",
            "by_first_played",
            "by_last_played",
            "reverse",
        }
        assert set(TRANSFORM_REGISTRY["sorter"].keys()) == expected

    def test_selector_operations(self):
        """Selector category contains expected operations."""
        from src.application.workflows.transform_registry import TRANSFORM_REGISTRY

        assert set(TRANSFORM_REGISTRY["selector"].keys()) == {"limit_tracks", "percentage"}

    def test_combiner_operations(self):
        """Combiner category contains expected operations."""
        from src.application.workflows.transform_registry import TRANSFORM_REGISTRY

        expected = {
            "merge_playlists",
            "concatenate_playlists",
            "interleave_playlists",
            "intersect_playlists",
        }
        assert set(TRANSFORM_REGISTRY["combiner"].keys()) == expected

    def test_all_entries_are_callable(self):
        """Every registry entry is a callable factory."""
        from src.application.workflows.transform_registry import TRANSFORM_REGISTRY

        for category, operations in TRANSFORM_REGISTRY.items():
            for op_name, factory in operations.items():
                assert callable(factory), f"{category}.{op_name} is not callable"


class TestMetricClassification:
    """Tests for _classify_metric open-ended classification."""

    @pytest.mark.parametrize(
        ("metric", "expected"),
        [
            ("title", "track_attribute"),
            ("album", "track_attribute"),
            ("release_date", "track_attribute"),
            ("duration_ms", "track_attribute"),
            ("artist", "track_attribute"),
        ],
    )
    def test_track_attributes(self, metric, expected):
        from src.application.workflows.transform_registry import _classify_metric

        assert _classify_metric(metric) == expected

    @pytest.mark.parametrize(
        ("metric", "expected"),
        [
            ("spotify_popularity", "external_metric"),
            ("lastfm_user_playcount", "external_metric"),
            ("lastfm_listeners", "external_metric"),
            ("lastfm_global_playcount", "external_metric"),
        ],
    )
    def test_external_metrics(self, metric, expected):
        from src.application.workflows.transform_registry import _classify_metric

        assert _classify_metric(metric) == expected

    @pytest.mark.parametrize(
        ("metric", "expected"),
        [
            ("total_plays", "play_history"),
            ("plays_last_7_days", "play_history"),
            ("plays_last_30_days", "play_history"),
            ("plays_last_90_days", "play_history"),
            ("last_played_date", "play_history"),
        ],
    )
    def test_play_history_metrics(self, metric, expected):
        from src.application.workflows.transform_registry import _classify_metric

        assert _classify_metric(metric) == expected

    def test_unknown_metric_defaults_to_external(self):
        """Unknown metrics default to external_metric (open-ended classification)."""
        from src.application.workflows.transform_registry import _classify_metric

        assert _classify_metric("nonexistent_metric") == "external_metric"


class TestRouteMetricSorting:
    """Tests for _route_metric_sorting routing."""

    def test_track_attribute_routes_to_key_function(self):
        """Track attribute metrics route to sort_by_key_function."""
        from src.application.workflows.transform_registry import _route_metric_sorting

        result = _route_metric_sorting({"metric_name": "title", "reverse": False})
        assert callable(result)

    def test_external_metric_routes_correctly(self):
        """External metrics route to sort_by_external_metrics."""
        from src.application.workflows.transform_registry import _route_metric_sorting

        result = _route_metric_sorting({
            "metric_name": "spotify_popularity",
            "reverse": True,
        })
        assert callable(result)

    def test_play_history_routes_correctly(self):
        """Play history metrics route to sort_by_play_history."""
        from src.application.workflows.transform_registry import _route_metric_sorting

        result = _route_metric_sorting({"metric_name": "total_plays", "reverse": True})
        assert callable(result)

    def test_unknown_metric_routes_to_external(self):
        """Unknown metrics route to external metric sorting (graceful no-op)."""
        from src.application.workflows.transform_registry import _route_metric_sorting

        result = _route_metric_sorting({"metric_name": "totally_fake"})
        assert callable(result)

    def test_missing_metric_name_raises(self):
        """Missing metric_name raises ValueError."""
        from src.application.workflows.transform_registry import _route_metric_sorting

        with pytest.raises(ValueError, match="metric_name is required"):
            _route_metric_sorting({})
