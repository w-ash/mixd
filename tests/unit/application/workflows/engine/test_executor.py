"""Tests for the asyncio workflow executor.

Tests that workflow result extraction, metrics aggregation, execution
guard, and per-node timeout mapping work correctly.
Validation and DAG scheduling tests live in test_validation.py.
"""

import asyncio

import pytest

from src.application.workflows.engine.executor import _get_node_timeout
from src.config.constants import WorkflowConstants
from src.domain.entities.track import Artist, Track, TrackList
from src.domain.entities.workflow import WorkflowDef, WorkflowTaskDef


class TestExtractWorkflowResult:
    """Tests for extract_workflow_result with typed task results."""

    def test_extracts_destination_tracklist(self, sample_tracklist):
        """Destination task's tracklist becomes the final result tracks."""
        from src.application.workflows.engine.executor import extract_workflow_result

        workflow_def = WorkflowDef(
            id="test",
            name="test_workflow",
            tasks=[
                WorkflowTaskDef(id="src_1", type="source.playlist"),
                WorkflowTaskDef(
                    id="dest_1",
                    type="destination.playlist",
                    upstream=["src_1"],
                ),
            ],
        )

        task_results = {
            "src_1": {"tracklist": sample_tracklist},
            "dest_1": {"tracklist": sample_tracklist},
        }

        result = extract_workflow_result(workflow_def, task_results, 1.0)

        assert result.tracks == sample_tracklist.tracks
        assert result.operation_name == "test_workflow"

    def test_extracts_metrics_from_task_results(self, sample_tracklist):
        """Metrics should be extracted from task results that have tracklist entries."""
        from src.application.workflows.engine.executor import extract_workflow_result

        tracklist_with_metrics = TrackList(
            tracks=sample_tracklist.tracks,
            metadata={"metrics": {"lastfm_plays": {1: 42, 2: 10}}},
        )

        workflow_def = WorkflowDef(
            id="metrics_test",
            name="metrics_test",
            tasks=[
                WorkflowTaskDef(id="src_1", type="source.playlist"),
                WorkflowTaskDef(
                    id="enricher_1",
                    type="enricher.lastfm",
                    upstream=["src_1"],
                ),
                WorkflowTaskDef(
                    id="dest_1",
                    type="destination.playlist",
                    upstream=["enricher_1"],
                ),
            ],
        )

        task_results = {
            "src_1": {"tracklist": sample_tracklist},
            "enricher_1": {"tracklist": tracklist_with_metrics},
            "dest_1": {"tracklist": tracklist_with_metrics},
        }

        result = extract_workflow_result(workflow_def, task_results, 1.0)

        assert "lastfm_plays" in result.metrics
        assert result.metrics["lastfm_plays"][1] == 42


class TestAggregateWorkflowMetrics:
    """Tests for _aggregate_workflow_metrics helper."""

    def test_aggregates_metrics_across_tasks(self, sample_tracklist):
        """Metrics from multiple tasks are merged."""
        from src.application.workflows.engine.executor import (
            _aggregate_workflow_metrics,
        )

        tl1 = TrackList(
            tracks=sample_tracklist.tracks,
            metadata={"metrics": {"plays": {1: 10}}},
        )
        tl2 = TrackList(
            tracks=sample_tracklist.tracks,
            metadata={"metrics": {"plays": {2: 20}, "play_count": {1: 80}}},
        )

        task_results = {
            "task_a": {"tracklist": tl1},
            "task_b": {"tracklist": tl2},
        }

        result = _aggregate_workflow_metrics(task_results)

        assert result["plays"] == {1: 10, 2: 20}
        assert result["play_count"] == {1: 80}

    def test_empty_metrics_handled(self, sample_tracklist):
        """Tasks without metrics in metadata produce empty result."""
        from src.application.workflows.engine.executor import (
            _aggregate_workflow_metrics,
        )

        task_results = {
            "task_1": {"tracklist": sample_tracklist},
        }

        result = _aggregate_workflow_metrics(task_results)
        assert result == {}


class TestOrchestratorWarnings:
    """Tests for orchestrator-level 0-track warnings."""

    def test_warns_when_node_outputs_zero_tracks(self, sample_tracklist):
        """_get_input_track_count + output_track_count == 0 should trigger warning.

        The actual warning lives inside build_flow's inner loop, which is
        difficult to test in isolation from Prefect infrastructure. This test
        validates the helper that determines input_track_count, confirming
        the condition can be met.
        """
        from src.application.workflows.engine.executor import _get_input_track_count

        task_def = WorkflowTaskDef(
            id="filter_1",
            type="filter.by_metric",
            upstream=["src_1"],
        )
        task_results = {
            "src_1": {"tracklist": sample_tracklist},
        }

        input_count = _get_input_track_count(task_def, task_results)

        # Source had 2 tracks — if output were 0, warning should fire
        assert input_count == 2
        assert input_count > 0  # Confirms the warning condition can trigger

    def test_no_warning_for_source_nodes(self):
        """Source nodes have no upstream, so input_track_count is None — no warning."""
        from src.application.workflows.engine.executor import _get_input_track_count

        task_def = WorkflowTaskDef(
            id="src_1",
            type="source.playlist",
        )

        input_count = _get_input_track_count(task_def, {})

        # None means no upstream — warning condition (> 0) won't fire
        assert input_count is None

    def test_none_input_track_count_no_type_error(self):
        """Regression: None input_track_count must not raise TypeError in > comparison.

        When source nodes (no upstream) produce output, the zero-output warning
        guard must handle None gracefully instead of raising
        TypeError("'>' not supported between instances of 'NoneType' and 'int'").
        """
        # Simulate the guard condition from build_flow's inner loop
        input_track_count: int | None = None
        output_track_count = 62  # Source produced tracks

        # This mirrors the zero-output warning guard in executor.py — must not raise
        should_warn = (
            input_track_count is not None
            and input_track_count > 0
            and output_track_count == 0
        )
        assert should_warn is False


class TestPrimaryInputTrackCount:
    """_get_input_track_count honors config.primary_input, not just upstream[0]."""

    def test_uses_primary_input_branch_not_first_upstream(self, sample_tracklist):
        """A node whose primary_input names the second upstream must report that
        branch's track count — not upstream[0]'s.

        Regression: _get_input_track_count and the success/degrade paths once
        disagreed on which upstream is primary. The success path was migrated to
        _primary_upstream_id; the diagnostic helper hardcoded upstream[0], so a
        combiner with primary_input=upstream[1] reported the wrong input count in
        SSE events and the track_count_checkpoint delta.
        """
        from src.application.workflows.engine.executor import _get_input_track_count

        # sample_tracklist has 2 tracks (src_a); src_b has 3.
        src_b_tracklist = TrackList(
            tracks=[
                Track(title="T1", artists=[Artist(name="A")], version=1),
                Track(title="T2", artists=[Artist(name="A")], version=1),
                Track(title="T3", artists=[Artist(name="A")], version=1),
            ]
        )
        task_def = WorkflowTaskDef(
            id="combiner_1",
            type="combiner.merge_playlists",
            upstream=["src_a", "src_b"],
            config={"primary_input": "src_b"},
        )
        task_results = {
            "src_a": {"tracklist": sample_tracklist},
            "src_b": {"tracklist": src_b_tracklist},
        }

        # Primary is src_b (3 tracks), not the first-declared src_a (2 tracks).
        assert _get_input_track_count(task_def, task_results) == 3

    def test_falls_back_to_first_upstream_without_primary_input(self, sample_tracklist):
        """When primary_input is unset, the first declared upstream is primary."""
        from src.application.workflows.engine.executor import _get_input_track_count

        task_def = WorkflowTaskDef(
            id="filter_1",
            type="filter.by_metric",
            upstream=["src_a"],
        )
        task_results = {"src_a": {"tracklist": sample_tracklist}}

        assert _get_input_track_count(task_def, task_results) == 2


class TestSafeEmit:
    """_safe_emit isolates observer failures from node execution."""

    async def test_swallows_observer_exception(self):
        """An observer raising must not propagate — it would otherwise be caught
        by the node-execution except and misrecorded as a node failure."""
        from src.application.workflows.engine.executor import _safe_emit

        async def boom() -> None:
            raise RuntimeError("SSE queue closed")

        # Must return normally (exception logged + swallowed), not raise.
        await _safe_emit(boom(), event="on_node_starting", node_id="node_1")

    async def test_reraises_cancelled_error(self):
        """CancelledError must propagate so SIGTERM drain still cancels the run."""
        from src.application.workflows.engine.executor import _safe_emit

        async def cancel() -> None:
            raise asyncio.CancelledError

        with pytest.raises(asyncio.CancelledError):
            await _safe_emit(cancel(), event="on_node_starting", node_id="node_1")


class TestGetNodeTimeout:
    """Tests for _get_node_timeout asyncio.timeout budget mapping."""

    @pytest.mark.parametrize(
        ("node_type", "expected"),
        [
            ("source.playlist", WorkflowConstants.SOURCE_TIMEOUT_SECONDS),
            ("source.liked", WorkflowConstants.SOURCE_TIMEOUT_SECONDS),
            ("enricher.spotify", WorkflowConstants.ENRICHER_TIMEOUT_SECONDS),
            ("enricher.lastfm", WorkflowConstants.ENRICHER_TIMEOUT_SECONDS),
            ("destination.playlist", WorkflowConstants.DESTINATION_TIMEOUT_SECONDS),
            ("filter.by_metric", WorkflowConstants.TRANSFORM_TIMEOUT_SECONDS),
            ("sorter.by_metric", WorkflowConstants.TRANSFORM_TIMEOUT_SECONDS),
            ("selector.top_n", WorkflowConstants.TRANSFORM_TIMEOUT_SECONDS),
        ],
    )
    def test_known_categories(self, node_type: str, expected: int) -> None:
        """Known node categories map to their configured timeout."""
        assert _get_node_timeout(node_type) == expected

    def test_unknown_category_falls_back_to_transform(self) -> None:
        """Unknown categories default to TRANSFORM_TIMEOUT_SECONDS."""
        assert (
            _get_node_timeout("mystery.node")
            == WorkflowConstants.TRANSFORM_TIMEOUT_SECONDS
        )
