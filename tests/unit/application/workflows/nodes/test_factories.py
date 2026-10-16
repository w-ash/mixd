"""Test node factory functions with comprehensive coverage."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.domain.entities.track import ArtistCredit, Track, TrackList
from tests.fixtures import TEST_USER_ID


class TestNodeFactories:
    """Test node factory functions."""

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
                Track(
                    title="Track C",
                    artists=[ArtistCredit(credited_name="Artist 3")],
                    version=1,
                    user_id=TEST_USER_ID,
                )
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
            apply_declared_defaults,
        )
        from src.application.workflows.nodes.factories import (
            build_play_history_enrichment_config,
        )

        ctx = MagicMock()
        with_defaults = build_play_history_enrichment_config(
            ctx, apply_declared_defaults("enricher.play_history", {})
        )
        assert with_defaults.metrics == ["total_plays", "last_played_dates"]

        # Without the executor's defaults there is no silent fallback: the
        # enrichment config refuses an empty metric list.
        with pytest.raises(ValueError, match="Metrics must be specified"):
            build_play_history_enrichment_config(ctx, {})

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


def _enricher_context(
    tracklist: TrackList, connector: object | None = None
) -> tuple[dict[str, object], AsyncMock, TrackList]:
    """Build a node context whose use case returns a distinct enriched tracklist."""
    enriched = TrackList(
        tracks=tracklist.tracks, metadata={"metrics": {"total_plays": {}}}
    )
    use_case_result = MagicMock()
    use_case_result.enriched_tracklist = enriched
    use_case_result.metrics_added = {"total_plays": {}}

    wf_ctx = AsyncMock()
    wf_ctx.user_id = "user-1"
    wf_ctx.use_cases = MagicMock()
    wf_ctx.connectors = MagicMock()
    wf_ctx.connectors.describe.return_value = MagicMock(
        capabilities=frozenset({"track_enrichment"})
    )
    wf_ctx.connectors.get_connector.return_value = connector
    wf_ctx.metric_config = MagicMock()
    wf_ctx.metric_config.get_connector_metrics.side_effect = lambda name: {
        "lastfm": ["lastfm_user_playcount"]
    }[name]
    wf_ctx.execute_use_case = AsyncMock(return_value=use_case_result)

    context: dict[str, object] = {
        "upstream_task_id": "src",
        "src": {"tracklist": tracklist},
        "workflow_context": wf_ctx,
    }
    return context, wf_ctx, enriched


class TestEnricherNodeFactory:
    """Enricher nodes hand the upstream tracklist to the enrich use case."""

    async def test_create_enricher_node_basic(self, sample_tracklist):
        """External enrichment resolves the named connector and its metric names."""
        from src.application.connector_protocols import TrackMetadataConnector
        from src.application.use_cases.enrich_tracks import EnrichmentConfig
        from src.application.workflows.nodes.factories import (
            build_external_enrichment_config,
            create_enricher_node,
        )

        connector = AsyncMock(spec=TrackMetadataConnector)
        context, wf_ctx, enriched = _enricher_context(sample_tracklist, connector)
        node_func = create_enricher_node(
            build_external_enrichment_config("lastfm"), enricher_label="lastfm"
        )

        result = await node_func(context, {})

        assert result == {"tracklist": enriched}
        getter, command = wf_ctx.execute_use_case.call_args.args
        assert getter is wf_ctx.use_cases.get_enrich_tracks_use_case
        assert command.user_id == "user-1"
        assert command.tracklist is sample_tracklist
        assert command.enrichment_config == EnrichmentConfig(
            enrichment_type="external_metadata",
            connector="lastfm",
            connector_instance=connector,
            track_metric_names=["lastfm_user_playcount"],
        )
        wf_ctx.connectors.describe.assert_called_once_with("lastfm")

    async def test_create_play_history_enricher_node(self, sample_tracklist):
        """Play-history enrichment carries the node's metrics and window."""
        from src.application.use_cases.enrich_tracks import EnrichmentConfig
        from src.application.workflows.nodes.factories import (
            build_play_history_enrichment_config,
            create_enricher_node,
        )

        context, wf_ctx, enriched = _enricher_context(sample_tracklist)
        node_func = create_enricher_node(build_play_history_enrichment_config)

        result = await node_func(
            context, {"metrics": ["total_plays"], "period_days": 30}
        )

        assert result == {"tracklist": enriched}
        getter, command = wf_ctx.execute_use_case.call_args.args
        assert getter is wf_ctx.use_cases.get_enrich_tracks_use_case
        assert command.tracklist is sample_tracklist
        assert command.enrichment_config == EnrichmentConfig(
            enrichment_type="play_history", metrics=["total_plays"], period_days=30
        )


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

        assert set(result) == {"tracklist"}
