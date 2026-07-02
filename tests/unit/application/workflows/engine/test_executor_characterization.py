"""Characterization tests pinning executor contracts ahead of the closure flatten.

Pins three previously-untested behaviors (fable-sweep spoke 11 lands these
BEFORE the refactor so the flatten is checked against them):

- the observer event sequence a run emits — the SSE progress stream
  (``RunHistoryObserver`` → ``node_status`` events) consumes this contract;
  the order and the degrade-skips-``on_node_completed`` rule are load-bearing;
- the node-timeout path: ``asyncio.timeout`` firing and the ``TimeoutError``
  rewrap carrying node identity to both the raise and the observer;
- the ``run_workflow`` seam (result assembly, ``**parameters`` splat,
  progress-broker completion) — previously exercised only with
  ``run_workflow`` itself mocked out.

All DAGs are chains (one node per level) so the global event order is
deterministic — nodes within a level interleave nondeterministically inside a
TaskGroup. Everything external is mocked; these run fast on the default gate
(deliberately no ``slow`` marker).
"""

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest

from src.application.workflows.engine.executor import build_flow, run_workflow
from src.domain.entities.progress import OperationStatus
from src.domain.entities.workflow import (
    NodeExecutionEvent,
    WorkflowDef,
    WorkflowTaskDef,
)


def _patch_env():
    """Patch the workflow context + session creation (mirrors test_fault_tolerance)."""
    mock_wf_ctx = AsyncMock()
    mock_wf_ctx.connectors.aclose = AsyncMock()

    @asynccontextmanager
    async def mock_get_session():
        yield AsyncMock()

    return (
        patch(
            "src.infrastructure.persistence.database.db_connection.get_session",
            mock_get_session,
        ),
        patch(
            "src.application.workflows.context.create_workflow_context",
            return_value=mock_wf_ctx,
        ),
    )


class _RecordingObserver:
    """Structural NodeExecutionObserver capturing the emitted call sequence."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.completed_events: dict[str, NodeExecutionEvent] = {}
        self.failed_errors: dict[str, Exception] = {}

    async def on_node_starting(self, event: NodeExecutionEvent) -> None:
        self.calls.append(("starting", event.task_def.id))

    async def on_node_completed(self, event: NodeExecutionEvent, result) -> None:
        self.calls.append(("completed", event.task_def.id))
        self.completed_events[event.task_def.id] = event

    async def on_node_failed(self, event: NodeExecutionEvent, error: Exception) -> None:
        self.calls.append(("failed", event.task_def.id))
        self.failed_errors[event.task_def.id] = error


def _chain_dag(middle_type: str = "filter.by_metric") -> WorkflowDef:
    """src → middle → dest chain; one node per level for deterministic order."""
    middle_config = (
        {"metric_name": "lastfm_plays"} if middle_type.startswith("filter.") else {}
    )
    return WorkflowDef(
        id="characterization",
        name="Characterization Workflow",
        tasks=[
            WorkflowTaskDef(
                id="src", type="source.playlist", config={"playlist_id": "p1"}
            ),
            WorkflowTaskDef(
                id="mid", type=middle_type, upstream=["src"], config=middle_config
            ),
            WorkflowTaskDef(
                id="dest",
                type="destination.update_playlist",
                upstream=["mid"],
                config={"playlist_id": "p1"},
            ),
        ],
    )


class TestObserverEventSequence:
    """The per-node observer sequence the SSE stream depends on."""

    async def test_happy_path_emits_starting_then_completed_per_node_in_order(
        self, sample_tracklist
    ):
        obs = _RecordingObserver()

        async def mock_execute_node(node_type, context, config):
            return {"tracklist": sample_tracklist}

        session_patch, ctx_patch = _patch_env()
        with (
            patch(
                "src.application.workflows.engine.executor.execute_node",
                side_effect=mock_execute_node,
            ),
            session_patch,
            ctx_patch,
        ):
            await build_flow(_chain_dag(), observer=obs)()

        assert obs.calls == [
            ("starting", "src"),
            ("completed", "src"),
            ("starting", "mid"),
            ("completed", "mid"),
            ("starting", "dest"),
            ("completed", "dest"),
        ]

        # Completed events carry track counts and timing for the SSE payload.
        for node_id in ("src", "mid", "dest"):
            event = obs.completed_events[node_id]
            assert event.output_track_count == 2
            assert event.duration_ms is not None
            assert event.duration_ms >= 0
        assert obs.completed_events["src"].input_track_count is None  # no upstream
        assert obs.completed_events["mid"].input_track_count == 2
        assert obs.completed_events["dest"].input_track_count == 2

    async def test_degraded_node_emits_failed_but_never_completed(
        self, sample_tracklist
    ):
        """Degrade emits on_node_failed, skips on_node_completed, and the run
        continues to completion with a status='degraded' record."""
        obs = _RecordingObserver()
        source_result = {"tracklist": sample_tracklist}

        async def mock_execute_node(node_type, context, config):
            if node_type == "source.playlist":
                return source_result
            if node_type == "enricher.lastfm":
                raise ConnectionError("Last.fm API is down")
            return {"tracklist": context.get("mid", source_result)["tracklist"]}

        session_patch, ctx_patch = _patch_env()
        with (
            patch(
                "src.application.workflows.engine.executor.execute_node",
                side_effect=mock_execute_node,
            ),
            session_patch,
            ctx_patch,
        ):
            context = await build_flow(
                _chain_dag(middle_type="enricher.lastfm"), observer=obs
            )()

        assert obs.calls == [
            ("starting", "src"),
            ("completed", "src"),
            ("starting", "mid"),
            ("failed", "mid"),
            ("starting", "dest"),
            ("completed", "dest"),
        ]
        assert isinstance(obs.failed_errors["mid"], ConnectionError)

        statuses = {r.node_id: r.status for r in context["_node_records"]}
        assert statuses == {"src": "completed", "mid": "degraded", "dest": "completed"}

    async def test_fatal_node_emits_failed_and_downstream_never_starts(
        self, sample_tracklist
    ):
        obs = _RecordingObserver()

        async def mock_execute_node(node_type, context, config):
            if node_type == "source.playlist":
                raise ConnectionError("Spotify is completely down")
            return {"tracklist": sample_tracklist}

        session_patch, ctx_patch = _patch_env()
        with (
            patch(
                "src.application.workflows.engine.executor.execute_node",
                side_effect=mock_execute_node,
            ),
            session_patch,
            ctx_patch,
        ):
            with pytest.raises(ConnectionError, match="Spotify is completely down"):
                await build_flow(_chain_dag(), observer=obs)()

        assert obs.calls == [("starting", "src"), ("failed", "src")]


class TestNodeTimeoutPath:
    """asyncio.timeout fires and the TimeoutError rewrap carries node identity."""

    async def test_timeout_rewraps_with_node_identity(self, sample_tracklist):
        # NOTE: patch _get_node_timeout, not WorkflowConstants —
        # _CATEGORY_TIMEOUTS snapshots the constants at import time, so
        # patching them post-import never takes effect.
        obs = _RecordingObserver()

        async def mock_execute_node(node_type, context, config):
            if node_type == "source.playlist":
                await asyncio.sleep(10)
            return {"tracklist": sample_tracklist}

        session_patch, ctx_patch = _patch_env()
        with (
            patch(
                "src.application.workflows.engine.executor.execute_node",
                side_effect=mock_execute_node,
            ),
            patch(
                "src.application.workflows.engine.executor._get_node_timeout",
                return_value=0.05,
            ),
            session_patch,
            ctx_patch,
        ):
            with pytest.raises(
                TimeoutError,
                match=r"Node 'src' \(source\.playlist\) exceeded 0\.05s timeout",
            ):
                await build_flow(_chain_dag(), observer=obs)()

        # The rewrapped exception (not the bare TimeoutError) reaches the
        # observer, so the SSE stream shows which node timed out.
        assert obs.calls == [("starting", "src"), ("failed", "src")]
        assert "exceeded 0.05s timeout" in str(obs.failed_errors["src"])


class TestRunWorkflowSeam:
    """run_workflow end-to-end: build, execute, assemble, progress completion."""

    @pytest.fixture
    def _load_catalog(self):
        # Import registers @node() definitions as a side effect (run_workflow's
        # one-time validate_registry() needs the catalog populated).
        from src.application.workflows.nodes import catalog

        assert catalog

    @staticmethod
    def _run_workflow_patches(mock_execute_node):
        """Patch stack for a real run_workflow call with no I/O side effects."""
        session_patch, ctx_patch = _patch_env()
        return (
            patch(
                "src.application.workflows.engine.executor.execute_node",
                side_effect=mock_execute_node,
            ),
            session_patch,
            ctx_patch,
            patch(
                "src.config.logging.add_workflow_run_logger",
                return_value="test-sink",
            ),
            patch("src.config.logging.remove_workflow_run_logger"),
        )

    @pytest.mark.usefixtures("_load_catalog")
    async def test_run_workflow_returns_result_and_completes_progress(
        self, sample_tracklist
    ):
        captured_params: dict[str, object] = {}

        async def mock_execute_node(node_type, context, config):
            if node_type == "source.playlist":
                captured_params.update(context["parameters"])
            return {"tracklist": sample_tracklist}

        broker = AsyncMock()
        broker.start_operation.return_value = "op-1"

        exec_patch, session_patch, ctx_patch, add_log, rm_log = (
            self._run_workflow_patches(mock_execute_node)
        )
        with exec_patch, session_patch, ctx_patch, add_log, rm_log as rm_mock:
            result = await run_workflow(
                _chain_dag(), progress_broker=broker, my_param="x"
            )

        assert result.operation_name == "Characterization Workflow"
        assert [t.title for t in result.tracks] == ["Track A", "Track B"]
        broker.complete_operation.assert_awaited_once_with(
            "op-1", OperationStatus.COMPLETED
        )
        # Dynamic parameters splat through to nodes; workflow_name is injected.
        assert captured_params["my_param"] == "x"
        assert captured_params["workflow_name"] == "Characterization Workflow"
        rm_mock.assert_called_once_with("test-sink")

    @pytest.mark.usefixtures("_load_catalog")
    async def test_run_workflow_marks_progress_failed_on_fatal(self, sample_tracklist):
        async def mock_execute_node(node_type, context, config):
            if node_type == "source.playlist":
                raise ConnectionError("Spotify is completely down")
            return {"tracklist": sample_tracklist}

        broker = AsyncMock()
        broker.start_operation.return_value = "op-1"

        exec_patch, session_patch, ctx_patch, add_log, rm_log = (
            self._run_workflow_patches(mock_execute_node)
        )
        with exec_patch, session_patch, ctx_patch, add_log, rm_log as rm_mock:
            with pytest.raises(ConnectionError, match="Spotify is completely down"):
                await run_workflow(_chain_dag(), progress_broker=broker)

        broker.complete_operation.assert_awaited_once_with(
            "op-1", OperationStatus.FAILED
        )
        rm_mock.assert_called_once_with("test-sink")
