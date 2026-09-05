"""Tests for transform definitions structure.

Validates TRANSFORM_REGISTRY and COMBINER_REGISTRY structure and completeness.
Metric classification and routing tests live in test_metric_routing.py.
"""


class TestTransformDefinitions:
    """Tests for TRANSFORM_REGISTRY and COMBINER_REGISTRY structure."""

    def test_all_categories_exist(self):
        """Registry has all expected top-level categories."""
        from src.application.workflows.nodes.transform_definitions import (
            TRANSFORM_REGISTRY,
        )

        expected = {"filter", "sorter", "selector"}
        assert set(TRANSFORM_REGISTRY.keys()) == expected

    def test_filter_operations(self):
        """Filter category contains expected operations."""
        from src.application.workflows.nodes.transform_definitions import (
            TRANSFORM_REGISTRY,
        )

        expected = {
            "deduplicate",
            "by_release_date",
            "by_release_year",
            "by_tracks",
            "by_artists",
            "by_metric",
            "by_play_history",
            "by_first_played_date",
            "by_preference",
            "by_tag",
            "by_tag_namespace",
            "by_duration",
            "by_liked_status",
            "by_explicit",
        }
        assert set(TRANSFORM_REGISTRY["filter"].keys()) == expected

    def test_sorter_operations(self):
        """Sorter category contains expected operations."""
        from src.application.workflows.nodes.transform_definitions import (
            TRANSFORM_REGISTRY,
        )

        expected = {
            "by_metric",
            "by_release_date",
            "by_play_history",
            "by_preference",
            "weighted_shuffle",
            "by_added_at",
            "by_first_played",
            "by_last_played",
            "reverse",
        }
        assert set(TRANSFORM_REGISTRY["sorter"].keys()) == expected

    def test_selector_operations(self):
        """Selector category contains expected operations."""
        from src.application.workflows.nodes.transform_definitions import (
            TRANSFORM_REGISTRY,
        )

        assert set(TRANSFORM_REGISTRY["selector"].keys()) == {
            "limit_tracks",
            "percentage",
        }

    def test_combiner_operations(self):
        """Combiner registry contains expected operations."""
        from src.application.workflows.nodes.transform_definitions import (
            COMBINER_REGISTRY,
        )

        expected = {
            "merge_playlists",
            "concatenate_playlists",
            "interleave_playlists",
            "intersect_playlists",
        }
        assert set(COMBINER_REGISTRY.keys()) == expected

    def test_all_transform_entries_are_callable(self):
        """Every transform definition entry has a callable factory."""
        from src.application.workflows.nodes.transform_definitions import (
            TRANSFORM_REGISTRY,
        )

        for category, operations in TRANSFORM_REGISTRY.items():
            for op_name, entry in operations.items():
                assert callable(entry.factory), (
                    f"{category}.{op_name} factory is not callable"
                )
                assert entry.description, f"{category}.{op_name} has no description"

    def test_all_combiner_entries_are_callable(self):
        """Every combiner registry entry has a callable fn."""
        from src.application.workflows.nodes.transform_definitions import (
            COMBINER_REGISTRY,
        )

        for op_name, entry in COMBINER_REGISTRY.items():
            assert callable(entry.fn), f"combiner.{op_name} fn is not callable"
            assert entry.description, f"combiner.{op_name} has no description"


class TestEnricherDependencyDeclarations:
    """Consumer entries declare the enricher dependency the validator reads."""

    def test_metric_consumers_name_their_metric_config_key(self):
        from src.application.workflows.nodes.transform_definitions import (
            TRANSFORM_REGISTRY,
        )

        for category in ("filter", "sorter"):
            entry = TRANSFORM_REGISTRY[category]["by_metric"]
            assert entry.metric_from_config == "metric_name"
            assert entry.requires_enricher is None

    def test_fixed_enricher_consumers_name_their_enricher(self):
        from src.application.workflows.nodes.transform_definitions import (
            TRANSFORM_REGISTRY,
        )

        expected = {
            ("filter", "by_preference"): ("enricher.preferences", None),
            ("sorter", "by_preference"): ("enricher.preferences", None),
            ("filter", "by_tag"): ("enricher.tags", None),
            ("filter", "by_tag_namespace"): ("enricher.tags", None),
            ("filter", "by_first_played_date"): (
                "enricher.play_history",
                "first_played_dates",
            ),
        }
        for (category, op_name), (enricher, metric) in expected.items():
            entry = TRANSFORM_REGISTRY[category][op_name]
            assert entry.requires_enricher == enricher, f"{category}.{op_name}"
            assert entry.requires_metric == metric, f"{category}.{op_name}"

    def test_entries_without_a_dependency_declare_none(self):
        """Intrinsic-data transforms carry no enricher dependency."""
        from src.application.workflows.nodes.transform_definitions import (
            TRANSFORM_REGISTRY,
        )

        entry = TRANSFORM_REGISTRY["filter"]["by_release_year"]
        assert entry.requires_enricher is None
        assert entry.requires_metric is None
        assert entry.metric_from_config is None

    def test_catalog_forwards_declarations_to_the_registry(self):
        from src.application.workflows.nodes import catalog
        from src.application.workflows.nodes.registry import list_nodes

        assert catalog
        nodes = list_nodes()
        assert nodes["filter.by_metric"]["metric_from_config"] == "metric_name"
        assert nodes["filter.by_first_played_date"]["requires_metric"] == (
            "first_played_dates"
        )
        assert "requires_enricher" not in nodes["filter.by_release_year"]
        play_history = nodes["enricher.play_history"]
        assert play_history["emits_metrics_from_config"] == "metrics"
        assert play_history["metric_config_corequisites"] == {
            "period_days": "period_plays"
        }
