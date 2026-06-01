"""TaskGroup level-executor semantics for the v0.8.1 asyncio swap.

These lock the behaviours that the gather→TaskGroup migration must preserve but
that had no test before the swap:

- independent nodes in a level run *concurrently* (not serialized);
- a fatal node lets its level siblings run to completion (option (a)), and the
  **first-submitted** fatal is the one raised (submission order, like gather);
- external cancellation surfaces as a **bare** ``CancelledError`` (never an
  ``ExceptionGroup``), so the run is recorded ``crashed`` one layer up;
- the event loop stays responsive while a node offloads CPU via ``to_thread``.
"""

import asyncio
from contextlib import asynccontextmanager
import time
from unittest.mock import AsyncMock, patch

import pytest

from src.application.workflows.engine.executor import build_flow
from src.domain.entities.workflow import WorkflowDef, WorkflowTaskDef


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


def _two_source_dag() -> WorkflowDef:
    """Two independent sources (level 0) feeding one destination (level 1)."""
    return WorkflowDef(
        id="concurrency",
        name="Concurrency",
        tasks=[
            WorkflowTaskDef(id="a", type="source.playlist", config={"role": "a"}),
            WorkflowTaskDef(id="b", type="source.playlist", config={"role": "b"}),
            WorkflowTaskDef(
                id="dest",
                type="destination.update_playlist",
                upstream=["a", "b"],
                config={"playlist_id": "p1"},
            ),
        ],
    )


@pytest.mark.slow
class TestLevelConcurrency:
    async def test_independent_nodes_run_concurrently(self, sample_tracklist):
        """Two nodes in a level make progress together — a rendezvous would
        dead-lock (and time out) if they were executed serially."""
        ev_a = asyncio.Event()
        ev_b = asyncio.Event()

        async def mock_execute_node(node_type, context, config):
            role = config.get("role")
            if role == "a":
                ev_a.set()
                await asyncio.wait_for(ev_b.wait(), timeout=2)
            elif role == "b":
                await asyncio.wait_for(ev_a.wait(), timeout=2)
                ev_b.set()
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
            # Bounded: serial execution would block forever on the rendezvous.
            await asyncio.wait_for(build_flow(_two_source_dag())(), timeout=3)


@pytest.mark.slow
class TestSiblingsFinishOnFatal:
    async def test_siblings_complete_and_first_submitted_fatal_wins(
        self, sample_tracklist
    ):
        """A fatal node does not cancel its siblings; they run to completion, and
        the first-submitted fatal (not the first to *finish*) is the one raised."""
        completed: set[str] = set()

        # Level 0: [a, b, c]. 'a' is fatal-slow, 'c' is fatal-fast (finishes first),
        # 'b' succeeds slowly. Submission order is a, b, c.
        workflow_def = WorkflowDef(
            id="siblings",
            name="Siblings",
            tasks=[
                WorkflowTaskDef(id="a", type="source.playlist", config={"role": "a"}),
                WorkflowTaskDef(id="b", type="source.playlist", config={"role": "b"}),
                WorkflowTaskDef(id="c", type="source.playlist", config={"role": "c"}),
                WorkflowTaskDef(
                    id="dest",
                    type="destination.update_playlist",
                    upstream=["a", "b", "c"],
                    config={"playlist_id": "p1"},
                ),
            ],
        )

        async def mock_execute_node(node_type, context, config):
            role = config.get("role")
            if role == "a":
                await asyncio.sleep(0.05)  # slow fatal (submitted first)
                raise ConnectionError("A-fatal")
            if role == "c":
                raise RuntimeError("C-fatal")  # fast fatal (submitted last)
            if role == "b":
                await asyncio.sleep(0.05)
                completed.add("b")
                return {"tracklist": sample_tracklist}
            return {"tracklist": sample_tracklist}  # dest (never reached)

        session_patch, ctx_patch = _patch_env()
        with (
            patch(
                "src.application.workflows.engine.executor.execute_node",
                side_effect=mock_execute_node,
            ),
            session_patch,
            ctx_patch,
        ):
            # First-submitted fatal ('a') wins even though 'c' raised first.
            with pytest.raises(ConnectionError, match="A-fatal"):
                await build_flow(workflow_def)()

        # The healthy sibling ran to completion despite 'a'/'c' failing.
        assert "b" in completed


@pytest.mark.slow
class TestExternalCancellation:
    async def test_external_cancel_surfaces_bare_cancellederror(self, sample_tracklist):
        """Cancelling the run mid-level raises a bare CancelledError, not an
        ExceptionGroup — so ExecuteWorkflowRunUseCase maps it to ``crashed``."""
        started = asyncio.Event()

        workflow_def = WorkflowDef(
            id="cancel",
            name="Cancel",
            tasks=[
                WorkflowTaskDef(
                    id="src", type="source.playlist", config={"playlist_id": "p1"}
                ),
                WorkflowTaskDef(
                    id="dest",
                    type="destination.update_playlist",
                    upstream=["src"],
                    config={"playlist_id": "p1"},
                ),
            ],
        )

        async def mock_execute_node(node_type, context, config):
            if node_type == "source.playlist":
                started.set()
                await asyncio.sleep(10)  # block so the level is in-flight
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
            task = asyncio.create_task(build_flow(workflow_def)())
            await asyncio.wait_for(started.wait(), timeout=1)
            task.cancel()

            with pytest.raises(BaseException) as exc_info:
                await task

        # Exactly CancelledError — not a (Base)ExceptionGroup wrapping it.
        assert type(exc_info.value) is asyncio.CancelledError


@pytest.mark.slow
class TestLoopResponsiveness:
    async def test_loop_stays_responsive_during_offloaded_node(self, sample_tracklist):
        """While a node offloads CPU via to_thread, a concurrent asyncio task keeps
        advancing — locking the invariant the heartbeat ticker depends on."""
        ticks = 0

        async def ticker():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.01)
                ticks += 1

        workflow_def = WorkflowDef(
            id="responsive",
            name="Responsive",
            tasks=[
                WorkflowTaskDef(
                    id="src", type="source.playlist", config={"playlist_id": "p1"}
                ),
                WorkflowTaskDef(
                    id="dest",
                    type="destination.update_playlist",
                    upstream=["src"],
                    config={"playlist_id": "p1"},
                ),
            ],
        )

        async def mock_execute_node(node_type, context, config):
            if node_type == "source.playlist":
                # Simulate a CPU-bound node offloaded to a worker thread, as the
                # real transform/combiner nodes do in node_factories.
                await asyncio.to_thread(time.sleep, 0.3)
            return {"tracklist": sample_tracklist}

        session_patch, ctx_patch = _patch_env()
        ticker_task = asyncio.create_task(ticker())
        try:
            with (
                patch(
                    "src.application.workflows.engine.executor.execute_node",
                    side_effect=mock_execute_node,
                ),
                session_patch,
                ctx_patch,
            ):
                await build_flow(workflow_def)()
        finally:
            ticker_task.cancel()

        # ~0.3s of offloaded work / 0.01s ticks ≈ 30 possible; a blocked loop ~0.
        assert ticks > 5
