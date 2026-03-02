"""Test node factory functions with comprehensive coverage."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestNodeFactories:
    """Test node factory functions."""

    def test_make_node_creates_functions(self):
        """Test make_node returns callable functions."""
        from src.application.workflows.node_factories import make_node

        # Test with actual categories and types from transform definitions
        node_configs = [
            ("filter", "deduplicate"),
            ("sorter", "by_metric"),
            ("selector", "limit_tracks"),
        ]

        for category, node_type in node_configs:
            node_func = make_node(category, node_type)
            assert callable(node_func)

    def test_make_node_invalid_category(self):
        """Test make_node with invalid category raises error."""
        from src.application.workflows.node_factories import make_node

        with pytest.raises(ValueError, match="Unknown node category: invalid_category"):
            make_node("invalid_category", "some_type")

    def test_make_node_invalid_type(self):
        """Test make_node with invalid type in valid category raises error."""
        from src.application.workflows.node_factories import make_node

        with pytest.raises(
            ValueError, match="Unknown node type: invalid_type in category filter"
        ):
            make_node("filter", "invalid_type")

    def test_make_node_rejects_combiner_category(self):
        """Combiners are no longer in TRANSFORM_REGISTRY — make_node should reject them."""
        from src.application.workflows.node_factories import make_node

        with pytest.raises(ValueError, match="Unknown node category: combiner"):
            make_node("combiner", "merge_playlists")


class TestCombinerNodeFactory:
    """Test combiner node creation."""

    def test_make_combiner_node_creates_functions(self):
        """Test make_combiner_node returns callable functions."""
        from src.application.workflows.node_factories import make_combiner_node

        for combiner_type in [
            "merge_playlists",
            "concatenate_playlists",
            "interleave_playlists",
            "intersect_playlists",
        ]:
            node_func = make_combiner_node(combiner_type)
            assert callable(node_func)

    def test_make_combiner_node_invalid_type(self):
        """Test make_combiner_node with invalid type raises error."""
        from src.application.workflows.node_factories import make_combiner_node

        with pytest.raises(ValueError, match="Unknown combiner type: nonexistent"):
            make_combiner_node("nonexistent")

    async def test_make_combiner_node_execution(self, sample_tracklist):
        """Test combiner node collects upstream tracklists and merges them."""
        from src.application.workflows.node_factories import make_combiner_node
        from src.domain.entities.track import Artist, Track, TrackList

        tl2 = TrackList(
            tracks=[Track(id=3, title="Track C", artists=[Artist(name="Artist 3")])]
        )

        context = {
            "upstream_task_ids": ["task_a", "task_b"],
            "task_a": {"tracklist": sample_tracklist},
            "task_b": {"tracklist": tl2},
        }

        node_func = make_combiner_node("merge_playlists")
        result = await node_func(context, {})

        assert "tracklist" in result
        assert len(result["tracklist"].tracks) == 3

    async def test_make_combiner_node_missing_upstream_raises(self):
        """Test combiner node raises when no upstream tasks provided."""
        from src.application.workflows.node_factories import make_combiner_node

        node_func = make_combiner_node("merge_playlists")

        with pytest.raises(ValueError, match="requires upstream tasks"):
            await node_func({}, {})


class TestEnricherNodeFactory:
    """Test enricher node creation."""

    @patch("src.application.workflows.node_factories.NodeContext")
    async def test_create_enricher_node_basic(
        self, mock_node_context_class, sample_tracklist
    ):
        """Test basic enricher node creation and execution."""
        from src.application.workflows.node_factories import create_enricher_node

        # Mock NodeContext
        mock_ctx = MagicMock()
        mock_ctx.extract_tracklist.return_value = sample_tracklist
        mock_ctx.get_connector.return_value = AsyncMock()
        mock_ctx.extract_use_cases.return_value = MagicMock()
        mock_node_context_class.return_value = mock_ctx

        # Mock use case execution
        mock_workflow_context = AsyncMock()
        mock_workflow_context.metric_config = MagicMock()
        mock_workflow_context.metric_config.get_connector_metrics.return_value = [
            "lastfm_user_playcount",
        ]
        mock_result = MagicMock()
        mock_result.enriched_tracklist = sample_tracklist
        mock_result.metrics_added = {"test_metric": [1, 2]}
        mock_result.errors = []
        mock_workflow_context.execute_use_case.return_value = mock_result
        mock_ctx.extract_workflow_context.return_value = mock_workflow_context

        config = {"connector": "lastfm"}
        node_func = create_enricher_node(config)

        context = {"test": "context"}
        node_config = {}

        result = await node_func(context, node_config)

        assert result["tracklist"] == sample_tracklist

    def test_create_enricher_node_missing_connector(self):
        """Test enricher node creation with missing connector."""
        from src.application.workflows.node_factories import create_enricher_node

        config = {}  # Missing connector

        with pytest.raises(
            ValueError, match="Enricher configuration must specify a 'connector' type"
        ):
            create_enricher_node(config)

    @patch("src.application.workflows.node_factories.NodeContext")
    async def test_create_play_history_enricher_node(
        self, mock_node_context_class, sample_tracklist
    ):
        """Test play history enricher node via unified create_enricher_node."""
        from src.application.workflows.node_factories import (
            build_play_history_enrichment_config,
            create_enricher_node,
        )

        # Mock NodeContext
        mock_ctx = MagicMock()
        mock_ctx.extract_tracklist.return_value = sample_tracklist
        mock_ctx.extract_use_cases.return_value = MagicMock()
        mock_ctx.extract_workflow_context.return_value = AsyncMock()
        mock_node_context_class.return_value = mock_ctx

        # Mock use case execution
        mock_workflow_context = AsyncMock()
        mock_result = MagicMock()
        mock_result.enriched_tracklist = sample_tracklist
        mock_result.metrics_added = {"total_plays": [5, 10]}
        mock_result.errors = []
        mock_workflow_context.execute_use_case.return_value = mock_result
        mock_ctx.extract_workflow_context.return_value = mock_workflow_context

        node_func = create_enricher_node(build_play_history_enrichment_config)

        context = {"test": "context"}
        config = {"metrics": ["total_plays"], "period_days": 30}

        result = await node_func(context, config)

        assert result["tracklist"] == sample_tracklist
