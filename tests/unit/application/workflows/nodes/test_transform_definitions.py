"""Tests for transform definitions structure.

Validates TRANSFORM_REGISTRY and COMBINER_REGISTRY structure and completeness.
Metric classification and routing tests live in test_metric_routing.py; the
enricher dependencies entries declare are exercised through the validator in
test_enrichment_dependency_validation.py.
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
            "by_artist_ids",
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
            "by_artist_name",
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

    def test_every_registry_entry_has_a_description(self):
        """The node palette shows each entry's description; none may be blank."""
        from src.application.workflows.nodes.transform_definitions import (
            COMBINER_REGISTRY,
            TRANSFORM_REGISTRY,
        )

        blank = [
            f"{category}.{op_name}"
            for category, operations in TRANSFORM_REGISTRY.items()
            for op_name, entry in operations.items()
            if not entry.description.strip()
        ] + [
            f"combiner.{op_name}"
            for op_name, entry in COMBINER_REGISTRY.items()
            if not entry.description.strip()
        ]
        assert blank == []
