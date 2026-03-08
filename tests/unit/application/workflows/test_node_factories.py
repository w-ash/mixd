"""Test node factory functions with comprehensive coverage."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.domain.entities.track import Artist, Track, TrackList


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


class TestTransformNodeWarnings:
    """Test that transform nodes warn on concerning outputs."""

    async def test_transform_warns_on_zero_output(self, sample_tracklist):
        """When a transform drops all tracks, it should log at WARNING."""
        from src.application.workflows.node_factories import make_node

        # by_metric with include_missing=False drops tracks without the metric.
        # sample_tracklist tracks have no metrics → all filtered out.
        node_func = make_node("filter", "by_metric")

        context = {
            "upstream_task_id": "src_1",
            "src_1": {"tracklist": sample_tracklist},
        }
        config = {
            "metric_name": "nonexistent",
            "min_value": 0,
            "include_missing": False,
        }

        with patch("src.application.workflows.node_factories.logger") as mock_logger:
            result = await node_func(context, config)

            assert len(result["tracklist"].tracks) == 0
            warning_calls = [str(call) for call in mock_logger.warning.call_args_list]
            assert any("filtered out" in w for w in warning_calls)


class TestEnricherNodeFactory:
    """Test enricher node creation."""

    @patch("src.application.workflows.node_factories.NodeContext")
    async def test_create_enricher_node_basic(
        self, mock_node_context_class, sample_tracklist
    ):
        """Test basic enricher node creation and execution."""
        from src.application.workflows.node_factories import (
            build_external_enrichment_config,
            create_enricher_node,
        )

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
        node_func = create_enricher_node(
            build_external_enrichment_config(config), enricher_label="lastfm"
        )

        context = {"test": "context"}
        node_config = {}

        result = await node_func(context, node_config)

        assert result["tracklist"] == sample_tracklist

    def test_create_enricher_node_missing_connector(self):
        """Test enricher node creation with missing connector."""
        from src.application.workflows.node_factories import (
            build_external_enrichment_config,
        )

        config: dict[str, str] = {}  # Missing connector

        with pytest.raises(
            ValueError, match="Enricher configuration must specify a 'connector' type"
        ):
            build_external_enrichment_config(config)

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

    @patch("src.application.workflows.node_factories.NodeContext")
    async def test_enricher_warns_on_total_failure(
        self, mock_node_context_class, sample_tracklist
    ):
        """When enrichment has errors and 0 metrics, warn about total failure."""
        from src.application.workflows.node_factories import create_enricher_node

        mock_ctx = MagicMock()
        mock_ctx.extract_tracklist.return_value = sample_tracklist
        mock_ctx.extract_use_cases.return_value = MagicMock()
        mock_node_context_class.return_value = mock_ctx

        mock_workflow_context = AsyncMock()
        mock_result = MagicMock()
        mock_result.enriched_tracklist = sample_tracklist
        mock_result.metrics_added = {}  # No metrics at all
        mock_result.errors = ["API timeout", "Rate limited"]
        mock_workflow_context.execute_use_case.return_value = mock_result
        mock_ctx.extract_workflow_context.return_value = mock_workflow_context

        build_config = MagicMock(return_value=MagicMock())
        node_func = create_enricher_node(build_config, enricher_label="lastfm")

        with patch("src.application.workflows.node_factories.logger") as mock_logger:
            await node_func({"test": "context"}, {})

            warning_calls = [str(call) for call in mock_logger.warning.call_args_list]
            assert any("failed completely" in w for w in warning_calls)


class TestTrackDecisionGeneration:
    """Test per-track decision generation helpers."""

    def _make_tracks(self, ids: list[int]) -> TrackList:
        return TrackList(
            tracks=[
                Track(id=i, title=f"Track {i}", artists=[Artist(name=f"Artist {i}")])
                for i in ids
            ]
        )

    def test_filter_decisions_marks_removed_tracks(self) -> None:
        from src.application.workflows.node_factories import _generate_filter_decisions

        input_tl = self._make_tracks([1, 2, 3])
        output_tl = self._make_tracks([1, 3])  # Track 2 filtered out
        config = {"metric_name": "play_count", "min_value": 5}

        decisions = _generate_filter_decisions(input_tl, output_tl, config)

        assert len(decisions) == 3
        kept = [d for d in decisions if d.decision == "kept"]
        removed = [d for d in decisions if d.decision == "removed"]
        assert len(kept) == 2
        assert len(removed) == 1
        assert removed[0].track_id == 2
        assert removed[0].metric_name == "play_count"
        assert removed[0].threshold == 5.0

    def test_sorter_decisions_include_rank(self) -> None:
        from src.application.workflows.node_factories import _generate_sorter_decisions

        output_tl = self._make_tracks([3, 1, 2])
        config = {"metric_name": "play_count"}

        decisions = _generate_sorter_decisions(output_tl, config)

        assert len(decisions) == 3
        assert all(d.decision == "kept" for d in decisions)
        assert decisions[0].rank == 1
        assert decisions[0].track_id == 3
        assert decisions[2].rank == 3

    def test_selector_decisions_marks_trimmed_tracks(self) -> None:
        from src.application.workflows.node_factories import (
            _generate_selector_decisions,
        )

        input_tl = self._make_tracks([1, 2, 3, 4, 5])
        output_tl = self._make_tracks([1, 2, 3])
        config = {"count": 3}

        decisions = _generate_selector_decisions(input_tl, output_tl, config)

        assert len(decisions) == 5
        kept = [d for d in decisions if d.decision == "kept"]
        removed = [d for d in decisions if d.decision == "removed"]
        assert len(kept) == 3
        assert len(removed) == 2

    async def test_make_node_returns_track_decisions(self, sample_tracklist) -> None:
        """Transform nodes include track_decisions in their result."""
        from src.application.workflows.node_factories import make_node

        # Deduplicate is a filter that keeps all (no dupes in sample)
        node_func = make_node("filter", "deduplicate")
        context = {
            "upstream_task_id": "src_1",
            "src_1": {"tracklist": sample_tracklist},
        }

        result = await node_func(context, {})

        assert "track_decisions" in result
        assert len(result["track_decisions"]) == 2  # Both tracks kept
