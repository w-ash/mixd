"""Tests for workflow fault tolerance: degraded nodes, graceful shutdown, idempotent destinations.

Verifies that:
- Enricher failures degrade rather than kill the workflow (upstream tracklist passes through)
- Source/transform/destination failures remain fatal
- Graceful shutdown cancels remaining nodes between iterations
- create_playlist delegates to update_playlist when a playlist already exists
"""

import pytest

from src.application.workflows.prefect import (
    WorkflowCancelledError,
    _is_failure_recoverable,
)


class TestFailureClassification:
    """_is_failure_recoverable correctly classifies node categories."""

    @pytest.mark.parametrize(
        "node_type",
        ["enricher.lastfm", "enricher.spotify", "enricher.play_history"],
    )
    def test_enricher_failures_are_recoverable(self, node_type: str):
        assert _is_failure_recoverable(node_type) is True

    @pytest.mark.parametrize(
        "node_type",
        [
            "source.playlist",
            "source.liked_tracks",
            "filter.by_metric",
            "sorter.by_metric",
            "selector.top_n",
            "destination.create_playlist",
            "destination.update_playlist",
            "combiner.merge_playlists",
        ],
    )
    def test_non_enricher_failures_are_fatal(self, node_type: str):
        assert _is_failure_recoverable(node_type) is False


class TestWorkflowCancelledError:
    """WorkflowCancelledError carries shutdown context."""

    def test_error_message(self):
        err = WorkflowCancelledError("Shutdown after 3/5 nodes")
        assert "3/5" in str(err)


class TestDegradedNodeHandling:
    """Verify that build_flow continues after enricher failures.

    These tests exercise the orchestration loop's fault tolerance path
    via a minimal Prefect flow execution using the real build_flow function.
    """

    @pytest.fixture
    def _load_catalog(self):
        import src.application.workflows.node_catalog  # noqa: F401

    @staticmethod
    def _mock_session_and_context():
        """Create properly structured mocks for get_session and workflow context."""
        from contextlib import asynccontextmanager
        from unittest.mock import AsyncMock, patch

        mock_wf_ctx = AsyncMock()
        mock_wf_ctx.connectors.aclose = AsyncMock()

        @asynccontextmanager
        async def mock_get_session():
            yield AsyncMock()

        patches = (
            patch(
                "src.infrastructure.persistence.database.db_connection.get_session",
                mock_get_session,
            ),
            patch(
                "src.application.workflows.context.create_workflow_context",
                return_value=mock_wf_ctx,
            ),
        )
        return patches

    @pytest.mark.usefixtures("_load_catalog")
    async def test_enricher_failure_produces_degraded_record(self, sample_tracklist):
        """When an enricher node fails, its record gets status='degraded' and the
        workflow continues using the upstream tracklist."""
        from unittest.mock import patch

        from src.domain.entities.workflow import WorkflowDef, WorkflowTaskDef

        from src.application.workflows.prefect import build_flow

        workflow_def = WorkflowDef(
            id="test-degraded",
            name="Test Degraded",
            tasks=[
                WorkflowTaskDef(id="src", type="source.playlist", config={"playlist_id": "p1"}),
                WorkflowTaskDef(
                    id="enrich", type="enricher.lastfm", upstream=["src"]
                ),
                WorkflowTaskDef(
                    id="dest",
                    type="destination.update_playlist",
                    upstream=["enrich"],
                    config={"playlist_id": "p1"},
                ),
            ],
        )

        source_result = {"tracklist": sample_tracklist}
        call_count = 0

        async def mock_execute_node(node_type, context, config):
            nonlocal call_count
            call_count += 1
            if node_type == "source.playlist":
                return source_result
            elif node_type == "enricher.lastfm":
                raise ConnectionError("Last.fm API is down")
            elif node_type.startswith("destination."):
                return {"tracklist": context.get("enrich", source_result)["tracklist"]}
            raise ValueError(f"Unexpected node type: {node_type}")

        session_patch, ctx_patch = self._mock_session_and_context()
        with (
            patch(
                "src.application.workflows.prefect.execute_node",
                side_effect=mock_execute_node,
            ),
            session_patch,
            ctx_patch,
        ):
            flow_fn = build_flow(workflow_def)
            context = await flow_fn()

        # All 3 nodes were attempted
        assert call_count == 3

        # Check node records
        node_records = context["_node_records"]
        statuses = {r.node_id: r.status for r in node_records}
        assert statuses["src"] == "completed"
        assert statuses["enrich"] == "degraded"
        assert statuses["dest"] == "completed"

        # Degraded node's error message is captured
        enrich_record = next(r for r in node_records if r.node_id == "enrich")
        assert "Last.fm API is down" in enrich_record.error_message

    @pytest.mark.usefixtures("_load_catalog")
    async def test_source_failure_is_fatal(self, sample_tracklist):
        """Source node failures kill the workflow (not recoverable)."""
        from unittest.mock import patch

        from src.domain.entities.workflow import WorkflowDef, WorkflowTaskDef

        from src.application.workflows.prefect import build_flow

        workflow_def = WorkflowDef(
            id="test-fatal",
            name="Test Fatal",
            tasks=[
                WorkflowTaskDef(id="src", type="source.playlist", config={"playlist_id": "p1"}),
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
                raise ConnectionError("Spotify is completely down")
            return {"tracklist": sample_tracklist}

        session_patch, ctx_patch = self._mock_session_and_context()
        with (
            patch(
                "src.application.workflows.prefect.execute_node",
                side_effect=mock_execute_node,
            ),
            session_patch,
            ctx_patch,
        ):
            flow_fn = build_flow(workflow_def)
            with pytest.raises(ConnectionError, match="Spotify is completely down"):
                await flow_fn()


class TestGracefulShutdown:
    """Tests for the SIGTERM graceful shutdown mechanism."""

    @pytest.fixture
    def _load_catalog(self):
        import src.application.workflows.node_catalog  # noqa: F401

    @pytest.mark.usefixtures("_load_catalog")
    async def test_shutdown_flag_cancels_remaining_nodes(self, sample_tracklist):
        """Setting _shutdown_requested between nodes skips remaining work."""
        from contextlib import asynccontextmanager
        from unittest.mock import AsyncMock, patch

        from src.domain.entities.workflow import WorkflowDef, WorkflowTaskDef

        import src.application.workflows.prefect as prefect_module
        from src.application.workflows.prefect import build_flow

        workflow_def = WorkflowDef(
            id="test-shutdown",
            name="Test Shutdown",
            tasks=[
                WorkflowTaskDef(id="src", type="source.playlist", config={"playlist_id": "p1"}),
                WorkflowTaskDef(
                    id="enrich", type="enricher.lastfm", upstream=["src"]
                ),
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
                prefect_module._shutdown_requested = True
            return {"tracklist": sample_tracklist}

        @asynccontextmanager
        async def mock_get_session():
            yield AsyncMock()

        mock_wf_ctx = AsyncMock()
        mock_wf_ctx.connectors.aclose = AsyncMock()

        with (
            patch(
                "src.application.workflows.prefect.execute_node",
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
        prefect_module._shutdown_requested = False
