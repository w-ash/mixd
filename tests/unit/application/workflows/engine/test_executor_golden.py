"""Golden regression snapshot for the workflow executor's OperationResult.

Drives the real ``build_flow`` execution path over a deterministic stub DAG
(source → filter → destination) with frozen track UUIDs and ``execution_time``
pinned to ``0.0``, then snapshots ``OperationResult.to_dict()`` to a committed
JSON fixture. (Originally the behaviour-preservation net for the v0.8.1
Prefect→asyncio swap.)

The fixture is the frozen baseline of the executor's serialized output; the
comparison is structural (parsed dicts) to stay robust against float/key-order
formatting. A diff here means the executor changed observable workflow output.
"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from src.application.workflows.engine.executor import (
    build_flow,
    extract_workflow_result,
)
from src.domain.entities.track import TrackList
from src.domain.entities.workflow import WorkflowDef, WorkflowTaskDef
from tests.fixtures import make_track

# Frozen UUIDs so the serialized snapshot is deterministic across runs.
_T1 = make_track(
    id="00000000-0000-0000-0000-000000000001",
    title="Frozen Track One",
    artist="Artist One",
)
_T2 = make_track(
    id="00000000-0000-0000-0000-000000000002",
    title="Frozen Track Two",
    artist="Artist Two",
)

_FIXTURE = Path(__file__).parent / "fixtures" / "golden_operation_result.json"


def _mock_session_and_context():
    """Patches for get_session + workflow context (mirrors test_fault_tolerance)."""
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock

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


@pytest.mark.slow
class TestGoldenOperationResult:
    """The end-to-end OperationResult shape is frozen across the engine swap."""

    @pytest.fixture
    def _load_catalog(self):
        from src.application.workflows.nodes import catalog

        assert catalog

    @pytest.mark.usefixtures("_load_catalog")
    async def test_operation_result_snapshot_is_stable(self):
        """build_flow → extract_workflow_result → to_dict matches the committed fixture."""
        workflow_def = WorkflowDef(
            id="golden-snapshot",
            name="Golden Snapshot Workflow",
            tasks=[
                WorkflowTaskDef(
                    id="src", type="source.playlist", config={"playlist_id": "p1"}
                ),
                WorkflowTaskDef(id="flt", type="filter.by_metric", upstream=["src"]),
                WorkflowTaskDef(
                    id="dest",
                    type="destination.update_playlist",
                    upstream=["flt"],
                    config={"playlist_id": "p1"},
                ),
            ],
        )

        # Deterministic per-node outputs. The source carries play metrics for both
        # tracks; the filter drops T2 (but T2's metric survives aggregation, proving
        # metrics merge across all task results, not just the final tracklist).
        source_tl = TrackList(
            tracks=[_T1, _T2],
            metadata={"metrics": {"lastfm_plays": {_T1.id: 10, _T2.id: 20}}},
        )
        filtered_tl = TrackList(
            tracks=[_T1],
            metadata={"metrics": {"lastfm_plays": {_T1.id: 10}}},
        )

        async def mock_execute_node(node_type, context, config):
            if node_type == "source.playlist":
                return {"tracklist": source_tl}
            if node_type == "filter.by_metric":
                return {"tracklist": filtered_tl}
            if node_type.startswith("destination."):
                return {"tracklist": filtered_tl}
            raise ValueError(f"Unexpected node type: {node_type}")

        session_patch, ctx_patch = _mock_session_and_context()
        with (
            patch(
                "src.application.workflows.engine.executor.execute_node",
                side_effect=mock_execute_node,
            ),
            session_patch,
            ctx_patch,
        ):
            context = await build_flow(workflow_def)()

        task_results = context["_task_results"]
        # execution_time pinned to 0.0 — it's wall-clock and would otherwise flake.
        result = extract_workflow_result(workflow_def, task_results, execution_time=0.0)
        actual = result.to_dict()

        # First capture (on the current engine) freezes the baseline fixture.
        if not _FIXTURE.exists():
            _FIXTURE.parent.mkdir(exist_ok=True)
            _FIXTURE.write_text(json.dumps(actual, indent=2, sort_keys=True) + "\n")

        expected = json.loads(_FIXTURE.read_text())
        # Structural compare (parsed dicts) — robust to float repr / key ordering.
        assert actual == expected
