"""Test node factory functions with comprehensive coverage."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.domain.entities.track import Artist, Track, TrackList


class TestNodeFactories:
    """Test node factory functions."""

    def test_make_node_creates_functions(self):
        """Test make_node returns callable functions."""
        from src.application.workflows.nodes.factories import make_node

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
        from src.application.workflows.nodes.factories import make_node

        with pytest.raises(ValueError, match="Unknown node category: invalid_category"):
            make_node("invalid_category", "some_type")

    def test_make_node_invalid_type(self):
        """Test make_node with invalid type in valid category raises error."""
        from src.application.workflows.nodes.factories import make_node

        with pytest.raises(
            ValueError, match="Unknown node type: invalid_type in category filter"
        ):
            make_node("filter", "invalid_type")

    def test_make_node_rejects_combiner_category(self):
        """Combiners are no longer in TRANSFORM_REGISTRY — make_node should reject them."""
        from src.application.workflows.nodes.factories import make_node

        with pytest.raises(ValueError, match="Unknown node category: combiner"):
            make_node("combiner", "merge_playlists")


class TestCombinerNodeFactory:
    """Test combiner node creation."""

    def test_make_combiner_node_creates_functions(self):
        """Test make_combiner_node returns callable functions."""
        from src.application.workflows.nodes.factories import make_combiner_node

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
        from src.application.workflows.nodes.factories import make_combiner_node

        with pytest.raises(ValueError, match="Unknown combiner type: nonexistent"):
            make_combiner_node("nonexistent")

    async def test_make_combiner_node_execution(self, sample_tracklist):
        """Test combiner node collects upstream tracklists and merges them."""
        from src.application.workflows.nodes.factories import make_combiner_node

        tl2 = TrackList(
            tracks=[
                Track(title="Track C", artists=[Artist(name="Artist 3")], version=1)
            ]
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
        from src.application.workflows.nodes.factories import make_combiner_node

        node_func = make_combiner_node("merge_playlists")

        with pytest.raises(ValueError, match="requires upstream tasks"):
            await node_func({}, {})


class TestDeclaredDefaultsFlowIntoNodes:
    """Node code restates no defaults: with ``apply_declared_defaults`` the
    declared value is what the transform sees, and without it the accessor's
    zero value is."""

    async def test_limit_tracks_with_empty_config_keeps_first_ten(self) -> None:
        from src.application.workflows.nodes.config_fields import (
            apply_declared_defaults,
        )
        from src.application.workflows.nodes.factories import make_node
        from tests.fixtures import make_persisted_track

        tracks = [make_persisted_track(title=f"T{i}") for i in range(15)]
        context = {
            "upstream_task_id": "src",
            "src": {"tracklist": TrackList(tracks=tracks)},
        }
        node_func = make_node("selector", "limit_tracks")

        result = await node_func(
            context, apply_declared_defaults("selector.limit_tracks", {})
        )

        assert [t.title for t in result["tracklist"].tracks] == [
            f"T{i}" for i in range(10)
        ]

    def test_play_history_builder_reads_declared_metrics(self) -> None:
        from src.application.workflows.nodes.config_fields import (
            DEFAULT_PLAY_HISTORY_METRICS,
            apply_declared_defaults,
        )
        from src.application.workflows.nodes.factories import (
            build_play_history_enrichment_config,
        )

        ctx = MagicMock()
        with_defaults = build_play_history_enrichment_config(
            ctx, apply_declared_defaults("enricher.play_history", {})
        )
        assert with_defaults.metrics == list(DEFAULT_PLAY_HISTORY_METRICS)

        # Without the executor's defaults there is no silent fallback: the
        # enrichment config refuses an empty metric list.
        with pytest.raises(ValueError, match="Metrics must be specified"):
            build_play_history_enrichment_config(ctx, {})

    def test_play_history_empty_metrics_falls_back_to_declared_defaults(self) -> None:
        """``{"metrics": []}`` runs with the defaults, as the validator promised."""
        from src.application.workflows.nodes.config_fields import (
            DEFAULT_PLAY_HISTORY_METRICS,
            apply_declared_defaults,
        )
        from src.application.workflows.nodes.factories import (
            build_play_history_enrichment_config,
        )

        for config in ({"metrics": []}, {"metrics": None}):
            built = build_play_history_enrichment_config(
                MagicMock(), apply_declared_defaults("enricher.play_history", config)
            )
            assert built.metrics == list(DEFAULT_PLAY_HISTORY_METRICS)

    async def test_limit_tracks_with_null_config_keeps_first_ten(self) -> None:
        """``{"count": null, "method": null}`` runs with the declared defaults."""
        from src.application.workflows.nodes.config_fields import (
            apply_declared_defaults,
        )
        from src.application.workflows.nodes.factories import make_node
        from tests.fixtures import make_persisted_track

        tracks = [make_persisted_track(title=f"T{i}") for i in range(15)]
        context = {
            "upstream_task_id": "src",
            "src": {"tracklist": TrackList(tracks=tracks)},
        }
        node_func = make_node("selector", "limit_tracks")

        result = await node_func(
            context,
            apply_declared_defaults(
                "selector.limit_tracks", {"count": None, "method": None}
            ),
        )

        assert [t.title for t in result["tracklist"].tracks] == [
            f"T{i}" for i in range(10)
        ]

    async def test_combiner_honors_declared_deduplicate(self, sample_tracklist) -> None:
        from src.application.workflows.nodes.config_fields import (
            apply_declared_defaults,
        )
        from src.application.workflows.nodes.factories import make_combiner_node

        context = {
            "upstream_task_ids": ["a", "b"],
            "a": {"tracklist": sample_tracklist},
            "b": {"tracklist": sample_tracklist},
        }
        node_func = make_combiner_node("merge_playlists")

        kept = await node_func(
            context, apply_declared_defaults("combiner.merge_playlists", {})
        )
        deduped = await node_func(context, {"deduplicate": True})

        assert len(kept["tracklist"].tracks) == 2 * len(sample_tracklist.tracks)
        assert len(deduped["tracklist"].tracks) == len(sample_tracklist.tracks)


class TestTransformNodeWarnings:
    """Test that transform nodes warn on concerning outputs."""

    async def test_transform_warns_on_zero_output(self, sample_tracklist):
        """When a transform drops all tracks, it should log at WARNING."""
        from src.application.workflows.nodes.factories import make_node

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

        with patch("src.application.workflows.nodes.factories.logger") as mock_logger:
            result = await node_func(context, config)

            assert len(result["tracklist"].tracks) == 0
            warning_calls = [str(call) for call in mock_logger.warning.call_args_list]
            assert any("filtered out" in w for w in warning_calls)


class TestTransformOffload:
    """Pure-CPU transforms run off the event loop so the heartbeat/SSE keep ticking."""

    async def test_transform_runs_off_event_loop_and_stays_responsive(
        self, monkeypatch, sample_tracklist
    ):
        """A blocking transform executes on a worker thread; the loop stays live.

        Without the ``asyncio.to_thread`` offload the transform would run on the
        event loop thread, the concurrent ticker could not advance, and a real
        heartbeat would be starved into a false ``crashed`` reap.
        """
        import asyncio
        import threading
        import time

        from src.application.workflows.nodes import factories
        from src.application.workflows.nodes.factories import make_node
        from src.application.workflows.nodes.transform_definitions import TransformEntry

        loop_thread = threading.get_ident()
        seen: dict[str, int] = {}

        def blocking_transform(tracklist):
            seen["thread"] = threading.get_ident()
            time.sleep(0.1)  # blocks the WORKER thread, not the event loop
            return tracklist

        monkeypatch.setitem(
            factories.TRANSFORM_REGISTRY["filter"],
            "deduplicate",
            TransformEntry(lambda _ctx, _cfg: blocking_transform, "test"),
        )

        node_func = make_node("filter", "deduplicate")
        context = {"upstream_task_id": "src", "src": {"tracklist": sample_tracklist}}

        ticks = 0

        async def ticker():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.01)
                ticks += 1

        ticker_task = asyncio.create_task(ticker())
        try:
            await node_func(context, {})
        finally:
            ticker_task.cancel()

        assert seen["thread"] != loop_thread  # ran on a worker thread
        assert ticks >= 1  # the loop kept making progress during the blocking call


class TestEnricherNodeFactory:
    """Test enricher node creation."""

    @patch("src.application.workflows.nodes.factories.NodeContext")
    async def test_create_enricher_node_basic(
        self, mock_node_context_class, sample_tracklist
    ):
        """Test basic enricher node creation and execution."""
        from src.application.workflows.nodes.factories import (
            build_external_enrichment_config,
            create_enricher_node,
        )

        # Mock NodeContext
        mock_ctx = MagicMock()
        mock_ctx.extract_tracklist.return_value = sample_tracklist
        mock_ctx.get_connector.return_value = AsyncMock()
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

        node_func = create_enricher_node(
            build_external_enrichment_config("lastfm"), enricher_label="lastfm"
        )

        context = {"test": "context"}
        node_config = {}

        result = await node_func(context, node_config)

        assert result["tracklist"] == sample_tracklist

    @patch("src.application.workflows.nodes.factories.NodeContext")
    async def test_create_play_history_enricher_node(
        self, mock_node_context_class, sample_tracklist
    ):
        """Test play history enricher node via unified create_enricher_node."""
        from src.application.workflows.nodes.factories import (
            build_play_history_enrichment_config,
            create_enricher_node,
        )

        # Mock NodeContext
        mock_ctx = MagicMock()
        mock_ctx.extract_tracklist.return_value = sample_tracklist
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


class TestTransformNodeResults:
    """Test that transform nodes return clean results without per-track decisions."""

    async def test_make_node_returns_tracklist_only(self, sample_tracklist) -> None:
        """Transform nodes return only tracklist, no per-track decisions."""
        from src.application.workflows.nodes.factories import make_node

        node_func = make_node("filter", "deduplicate")
        context = {
            "upstream_task_id": "src_1",
            "src_1": {"tracklist": sample_tracklist},
        }

        result = await node_func(context, {})

        assert "tracklist" in result
        assert "track_decisions" not in result
        assert "node_details" not in result
