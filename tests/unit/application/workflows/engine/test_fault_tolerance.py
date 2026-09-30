"""Tests for workflow fault tolerance: failure classification and graceful shutdown.

Verifies that:
- Enricher failures are classified recoverable; all other categories are fatal
- Graceful shutdown cancels remaining nodes between iterations
- Connector cleanup survives cancellation

The degrade and fatal paths through build_flow live in
test_executor_characterization.py.
"""

import pytest

from src.application.workflows.engine.executor import (
    WorkflowCancelledError,
    _is_failure_recoverable,
)
from src.config.constants import NodeType


class TestFailureClassification:
    """_is_failure_recoverable correctly classifies node categories."""

    def test_enricher_failures_are_recoverable(self):
        assert _is_failure_recoverable("enricher") is True

    @pytest.mark.parametrize(
        "category",
        [
            "source",
            "filter",
            "sorter",
            "selector",
            "destination",
            "combiner",
        ],
    )
    def test_non_enricher_failures_are_fatal(self, category: NodeType):
        assert _is_failure_recoverable(category) is False


@pytest.mark.slow
class TestGracefulShutdown:
    """Tests for the SIGTERM graceful shutdown mechanism."""

    @pytest.fixture
    def _load_catalog(self):
        # Import registers @node() definitions as a side effect; the reference
        # keeps F401 from flagging it under ruff configs that autofix noqa.
        from src.application.workflows.nodes import catalog

        assert catalog

    @pytest.mark.usefixtures("_load_catalog")
    async def test_shutdown_flag_cancels_remaining_nodes(self, sample_tracklist):
        """Setting _shutdown_requested between nodes skips remaining work."""
        from contextlib import asynccontextmanager
        from unittest.mock import AsyncMock, patch

        import src.application.workflows.engine.executor as executor_module
        from src.application.workflows.engine.executor import build_flow
        from src.domain.entities.workflow import WorkflowDef, WorkflowTaskDef

        workflow_def = WorkflowDef(
            id="test-shutdown",
            name="Test Shutdown",
            tasks=[
                WorkflowTaskDef(
                    id="src", type="source.playlist", config={"playlist_id": "p1"}
                ),
                WorkflowTaskDef(id="enrich", type="enricher.lastfm", upstream=["src"]),
                WorkflowTaskDef(
                    id="dest",
                    type="destination.update_playlist",
                    upstream=["enrich"],
                    config={"playlist_id": "p1"},
                ),
            ],
        )

        call_count = 0

        async def mock_execute_node(node_type, context, config):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # After first node completes, trigger shutdown
                executor_module._shutdown_requested = True
            return {"tracklist": sample_tracklist}

        @asynccontextmanager
        async def mock_get_session():
            yield AsyncMock()

        mock_wf_ctx = AsyncMock()
        mock_wf_ctx.connectors.aclose = AsyncMock()

        with (
            patch(
                "src.application.workflows.engine.executor.execute_node",
                side_effect=mock_execute_node,
            ),
            patch(
                "src.infrastructure.persistence.database.db_connection.get_session",
                mock_get_session,
            ),
            patch(
                "src.application.workflows.context.create_workflow_context",
                return_value=mock_wf_ctx,
            ),
        ):
            flow_fn = build_flow(workflow_def)
            with pytest.raises(WorkflowCancelledError, match="1/3 nodes"):
                await flow_fn()

        # Only 1 node executed before shutdown
        assert call_count == 1

        # Reset the flag for other tests
        executor_module._shutdown_requested = False

    @pytest.mark.usefixtures("_load_catalog")
    async def test_connector_cleanup_survives_cancellation(self, sample_tracklist):
        """A CancelledError during cleanup (SIGTERM) must not abort aclose().

        The cleanup finally shields ``connectors.aclose()`` so a deploy/autoscale
        cancellation can't interrupt the close mid-flight and leak httpx2 pools.
        Without the shield, cancelling the flow task mid-aclose would leave the
        connectors open (``closed`` never set) and this test would time out.
        """
        import asyncio
        import contextlib
        from contextlib import asynccontextmanager
        from unittest.mock import AsyncMock, patch

        from src.application.workflows.engine.executor import build_flow
        from src.domain.entities.workflow import WorkflowDef, WorkflowTaskDef

        workflow_def = WorkflowDef(
            id="test-shield",
            name="Test Shield",
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

        started = asyncio.Event()
        done = asyncio.Event()
        closed = False

        async def slow_aclose() -> None:
            nonlocal closed
            started.set()  # cleanup has begun; the test now cancels the task
            await asyncio.sleep(0.05)
            closed = True
            done.set()

        async def mock_execute_node(node_type, context, config):
            return {"tracklist": sample_tracklist}

        @asynccontextmanager
        async def mock_get_session():
            yield AsyncMock()

        mock_wf_ctx = AsyncMock()
        mock_wf_ctx.connectors.aclose = slow_aclose

        with (
            patch(
                "src.application.workflows.engine.executor.execute_node",
                side_effect=mock_execute_node,
            ),
            patch(
                "src.infrastructure.persistence.database.db_connection.get_session",
                mock_get_session,
            ),
            patch(
                "src.application.workflows.context.create_workflow_context",
                return_value=mock_wf_ctx,
            ),
        ):
            task = asyncio.create_task(build_flow(workflow_def)())
            await asyncio.wait_for(started.wait(), timeout=1)
            task.cancel()  # SIGTERM arrives mid-cleanup
            with contextlib.suppress(asyncio.CancelledError):
                await task

            # The shielded aclose runs to completion despite the cancellation.
            await asyncio.wait_for(done.wait(), timeout=1)
            assert closed is True
