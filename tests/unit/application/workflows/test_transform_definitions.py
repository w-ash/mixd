"""Tests for transform definitions structure.

Validates TRANSFORM_REGISTRY and COMBINER_REGISTRY structure and completeness.
Metric classification and routing tests live in test_metric_routing.py.
"""


class TestTransformDefinitions:
    """Tests for TRANSFORM_REGISTRY and COMBINER_REGISTRY structure."""

    def test_all_categories_exist(self):
        """Registry has all expected top-level categories."""
        from src.application.workflows.transform_definitions import TRANSFORM_REGISTRY

        expected = {"filter", "sorter", "selector"}
        assert set(TRANSFORM_REGISTRY.keys()) == expected

    def test_filter_operations(self):
        """Filter category contains expected operations."""
        from src.application.workflows.transform_definitions import TRANSFORM_REGISTRY

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
        from src.application.workflows.transform_definitions import TRANSFORM_REGISTRY

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
        from src.application.workflows.transform_definitions import TRANSFORM_REGISTRY

        assert set(TRANSFORM_REGISTRY["selector"].keys()) == {
            "limit_tracks",
            "percentage",
        }

    def test_combiner_operations(self):
        """Combiner registry contains expected operations."""
        from src.application.workflows.transform_definitions import COMBINER_REGISTRY

        expected = {
            "merge_playlists",
            "concatenate_playlists",
            "interleave_playlists",
            "intersect_playlists",
        }
        assert set(COMBINER_REGISTRY.keys()) == expected

    def test_all_transform_entries_are_callable(self):
        """Every transform definition entry has a callable factory."""
        from src.application.workflows.transform_definitions import TRANSFORM_REGISTRY

        for category, operations in TRANSFORM_REGISTRY.items():
            for op_name, entry in operations.items():
                assert callable(entry.factory), (
                    f"{category}.{op_name} factory is not callable"
                )
                assert entry.description, f"{category}.{op_name} has no description"

    def test_all_combiner_entries_are_callable(self):
        """Every combiner registry entry has a callable fn."""
        from src.application.workflows.transform_definitions import COMBINER_REGISTRY

        for op_name, entry in COMBINER_REGISTRY.items():
            assert callable(entry.fn), f"combiner.{op_name} fn is not callable"
            assert entry.description, f"combiner.{op_name} has no description"
