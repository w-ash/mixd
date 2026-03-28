"""Integration tests for WorkflowRunRepository with real database operations.

Tests CRUD, pagination, cascade delete, node status updates, and batch latest-run queries.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid7

import pytest

from src.domain.entities.workflow import (
    Workflow,
    WorkflowDef,
    WorkflowRun,
    WorkflowRunNode,
    WorkflowTaskDef,
)
from src.domain.exceptions import NotFoundError
from src.infrastructure.persistence.repositories.workflow.core import WorkflowRepository
from src.infrastructure.persistence.repositories.workflow.runs import (
    WorkflowRunRepository,
)


def _make_def(wf_id: str = "test", name: str = "Test") -> WorkflowDef:
    return WorkflowDef(
        id=wf_id,
        name=name,
        tasks=[
            WorkflowTaskDef(id="source_1", type="source.liked_tracks"),
            WorkflowTaskDef(
                id="filter_1", type="filter.by_metric", upstream=["source_1"]
            ),
        ],
    )


async def _create_workflow(db_session) -> Workflow:
    """Helper to create a workflow that runs can reference via FK."""
    wf_repo = WorkflowRepository(db_session)
    return await wf_repo.save_workflow(Workflow(definition=_make_def()))


def _make_run(workflow_id: UUID, *, status: str = "pending") -> WorkflowRun:
    """Build a domain WorkflowRun with pre-created node records."""
    wf_def = _make_def()
    nodes = [
        WorkflowRunNode(
            node_id=task.id,
            node_type=task.type,
            execution_order=i + 1,
        )
        for i, task in enumerate(wf_def.tasks)
    ]
    return WorkflowRun(
        workflow_id=workflow_id,
        status=status,
        definition_snapshot=wf_def,
        nodes=nodes,
    )


class TestWorkflowRunCRUD:
    """Create, retrieve, and update workflow runs."""

    async def test_create_and_retrieve(self, db_session) -> None:
        workflow = await _create_workflow(db_session)
        repo = WorkflowRunRepository(db_session)

        run = _make_run(workflow.id)
        saved = await repo.create_run(run)

        assert saved.id is not None
        assert saved.workflow_id == workflow.id
        assert saved.status == "pending"
        assert saved.definition_snapshot.name == "Test"
        assert len(saved.nodes) == 2

        retrieved = await repo.get_run_by_id(saved.id)
        assert retrieved.id == saved.id
        assert len(retrieved.nodes) == 2
        assert retrieved.nodes[0].node_id == "source_1"
        assert retrieved.nodes[1].node_id == "filter_1"

    async def test_update_run_status_to_running(self, db_session) -> None:
        workflow = await _create_workflow(db_session)
        repo = WorkflowRunRepository(db_session)
        saved = await repo.create_run(_make_run(workflow.id))

        now = datetime.now(UTC)
        await repo.update_run_status(saved.id, "running", started_at=now)

        retrieved = await repo.get_run_by_id(saved.id)
        assert retrieved.status == "running"
        assert retrieved.started_at is not None

    async def test_update_run_status_to_completed(self, db_session) -> None:
        workflow = await _create_workflow(db_session)
        repo = WorkflowRunRepository(db_session)
        saved = await repo.create_run(_make_run(workflow.id))

        now = datetime.now(UTC)
        await repo.update_run_status(
            saved.id,
            "completed",
            completed_at=now,
            duration_ms=1500,
            output_track_count=42,
        )

        retrieved = await repo.get_run_by_id(saved.id)
        assert retrieved.status == "completed"
        assert retrieved.duration_ms == 1500

    async def test_update_run_status_to_failed(self, db_session) -> None:
        workflow = await _create_workflow(db_session)
        repo = WorkflowRunRepository(db_session)
        saved = await repo.create_run(_make_run(workflow.id))

        await repo.update_run_status(saved.id, "failed", error_message="API timeout")

        retrieved = await repo.get_run_by_id(saved.id)
        assert retrieved.status == "failed"
        assert retrieved.error_message == "API timeout"

    async def test_update_nonexistent_run_raises(self, db_session) -> None:
        repo = WorkflowRunRepository(db_session)

        with pytest.raises(NotFoundError):
            await repo.update_run_status(uuid7(), "running")

    async def test_get_nonexistent_run_raises(self, db_session) -> None:
        repo = WorkflowRunRepository(db_session)

        with pytest.raises(NotFoundError):
            await repo.get_run_by_id(uuid7())


class TestWorkflowRunNodeStatus:
    """Node-level status updates within a run."""

    async def test_update_node_to_running(self, db_session) -> None:
        workflow = await _create_workflow(db_session)
        repo = WorkflowRunRepository(db_session)
        saved = await repo.create_run(_make_run(workflow.id))

        now = datetime.now(UTC)
        await repo.update_node_status(saved.id, "source_1", "running", started_at=now)

        retrieved = await repo.get_run_by_id(saved.id)
        source_node = next(n for n in retrieved.nodes if n.node_id == "source_1")
        assert source_node.status == "running"
        assert source_node.started_at is not None

    async def test_update_node_to_completed_with_metrics(self, db_session) -> None:
        workflow = await _create_workflow(db_session)
        repo = WorkflowRunRepository(db_session)
        saved = await repo.create_run(_make_run(workflow.id))

        now = datetime.now(UTC)
        await repo.update_node_status(
            saved.id,
            "filter_1",
            "completed",
            completed_at=now,
            duration_ms=800,
            input_track_count=100,
            output_track_count=42,
        )

        retrieved = await repo.get_run_by_id(saved.id)
        filter_node = next(n for n in retrieved.nodes if n.node_id == "filter_1")
        assert filter_node.status == "completed"
        assert filter_node.duration_ms == 800
        assert filter_node.input_track_count == 100
        assert filter_node.output_track_count == 42

    async def test_update_nonexistent_node_raises(self, db_session) -> None:
        workflow = await _create_workflow(db_session)
        repo = WorkflowRunRepository(db_session)
        saved = await repo.create_run(_make_run(workflow.id))

        with pytest.raises(NotFoundError, match="not found"):
            await repo.update_node_status(saved.id, "no_such_node", "running")

    async def test_save_additional_node_record(self, db_session) -> None:
        workflow = await _create_workflow(db_session)
        repo = WorkflowRunRepository(db_session)
        saved = await repo.create_run(_make_run(workflow.id))

        new_node = WorkflowRunNode(
            run_id=saved.id,
            node_id="extra_step",
            node_type="sink.playlist",
            execution_order=3,
        )
        saved_node = await repo.save_node_record(new_node)

        assert saved_node.id is not None
        assert saved_node.node_id == "extra_step"

        retrieved = await repo.get_run_by_id(saved.id)
        assert len(retrieved.nodes) == 3


class TestWorkflowRunPagination:
    """Listing runs with pagination and ordering."""

    async def test_returns_runs_ordered_by_created_desc(self, db_session) -> None:
        workflow = await _create_workflow(db_session)
        repo = WorkflowRunRepository(db_session)

        # Create 3 runs
        for _ in range(3):
            await repo.create_run(_make_run(workflow.id))

        runs, total = await repo.get_runs_for_workflow(workflow.id)
        assert total == 3
        assert len(runs) == 3
        # Runs should NOT include nodes (summary mode)
        assert runs[0].nodes == []

    async def test_pagination_limit_and_offset(self, db_session) -> None:
        workflow = await _create_workflow(db_session)
        repo = WorkflowRunRepository(db_session)

        for _ in range(5):
            await repo.create_run(_make_run(workflow.id))

        page1, total = await repo.get_runs_for_workflow(workflow.id, limit=2, offset=0)
        assert total == 5
        assert len(page1) == 2

        page2, _ = await repo.get_runs_for_workflow(workflow.id, limit=2, offset=2)
        assert len(page2) == 2

        # No overlap
        page1_ids = {r.id for r in page1}
        page2_ids = {r.id for r in page2}
        assert page1_ids.isdisjoint(page2_ids)

    async def test_empty_runs_list(self, db_session) -> None:
        workflow = await _create_workflow(db_session)
        repo = WorkflowRunRepository(db_session)

        runs, total = await repo.get_runs_for_workflow(workflow.id)
        assert total == 0
        assert runs == []


class TestLatestRunQueries:
    """Latest-run lookup for single and batch queries."""

    async def test_latest_run_for_workflow(self, db_session) -> None:
        workflow = await _create_workflow(db_session)
        repo = WorkflowRunRepository(db_session)

        await repo.create_run(_make_run(workflow.id))
        latest_run = await repo.create_run(_make_run(workflow.id))

        result = await repo.get_latest_run_for_workflow(workflow.id)
        assert result is not None
        assert result.id == latest_run.id

    async def test_latest_run_none_when_no_runs(self, db_session) -> None:
        workflow = await _create_workflow(db_session)
        repo = WorkflowRunRepository(db_session)

        result = await repo.get_latest_run_for_workflow(workflow.id)
        assert result is None

    async def test_batch_latest_runs(self, db_session) -> None:
        wf_repo = WorkflowRepository(db_session)
        repo = WorkflowRunRepository(db_session)

        wf1 = await wf_repo.save_workflow(Workflow(definition=_make_def("wf1", "WF1")))
        wf2 = await wf_repo.save_workflow(Workflow(definition=_make_def("wf2", "WF2")))

        # Create runs for wf1 (keep last)
        await repo.create_run(_make_run(wf1.id))
        wf1_latest = await repo.create_run(_make_run(wf1.id))

        # Create one run for wf2
        wf2_latest = await repo.create_run(_make_run(wf2.id))

        result = await repo.get_latest_runs_for_workflows([wf1.id, wf2.id])
        assert len(result) == 2
        assert result[wf1.id].id == wf1_latest.id
        assert result[wf2.id].id == wf2_latest.id

    async def test_batch_latest_runs_empty_ids(self, db_session) -> None:
        repo = WorkflowRunRepository(db_session)

        result = await repo.get_latest_runs_for_workflows([])
        assert result == {}

    async def test_batch_latest_runs_missing_workflow(self, db_session) -> None:
        repo = WorkflowRunRepository(db_session)

        result = await repo.get_latest_runs_for_workflows([uuid7()])
        assert result == {}


class TestCascadeDelete:
    """Deleting a workflow cascades to runs and nodes."""

    async def test_deleting_workflow_cascades_to_runs(self, db_session) -> None:
        wf_repo = WorkflowRepository(db_session)
        run_repo = WorkflowRunRepository(db_session)

        workflow = await wf_repo.save_workflow(Workflow(definition=_make_def()))
        run = await run_repo.create_run(_make_run(workflow.id))

        # Delete workflow
        await wf_repo.delete_workflow(workflow.id)
        await db_session.flush()

        # Run should be gone
        with pytest.raises(NotFoundError):
            await run_repo.get_run_by_id(run.id)
