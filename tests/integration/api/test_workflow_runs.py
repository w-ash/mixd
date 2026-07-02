"""Integration tests for workflow run API endpoints.

Tests POST /run (202, 409, 404), GET /runs (pagination),
and GET /runs/{run_id} (200, 404, with nodes).
"""

import asyncio

import httpx
import pytest

from src.interface.api.services.progress import get_operation_registry
import src.interface.api.services.workflow_execution as _workflow_execution_mod
from tests.fixtures.factories import nonexistent_id
from tests.integration.api.conftest import create_workflow as _create_workflow


@pytest.fixture(autouse=True)
def _stub_workflow_background(monkeypatch):
    """Prevent background workflow execution in tests — only verify endpoints.

    ``POST /workflows/{id}/run`` kicks off the background task via
    ``launch_workflow_run`` in ``workflow_execution``, so that is the module
    whose ``launch_background`` binding must be stubbed.
    """

    def _noop_launch(_name: str, _coro_factory: object, **_kwargs: object) -> None:
        pass

    monkeypatch.setattr(_workflow_execution_mod, "launch_background", _noop_launch)


async def _seed_completed_runs(workflow_id: str, count: int) -> None:
    """Insert ``count`` COMPLETED run rows for a workflow via the repository.

    Terminal runs sidestep the active-run guard (uq_workflow_runs_active only
    covers pending/running), so this builds run history without stacking active
    POSTs — the realistic state the list/pagination endpoints operate on.
    """
    from uuid import UUID

    from src.domain.entities.workflow import WorkflowDef, WorkflowRun
    from src.infrastructure.persistence.database.db_connection import get_session
    from src.infrastructure.persistence.repositories.workflow.runs import (
        WorkflowRunRepository,
    )

    snapshot = WorkflowDef(id="test-wf", name="Test Workflow")
    async with get_session(rollback=False) as session:
        repo = WorkflowRunRepository(session)
        for _ in range(count):
            await repo.create_run(
                WorkflowRun(
                    workflow_id=UUID(workflow_id),
                    status="completed",
                    definition_snapshot=snapshot,
                )
            )
        await session.commit()


class TestRunWorkflowEndpoint:
    """POST /workflows/{id}/run — starts execution."""

    async def test_run_returns_202(self, client: httpx.AsyncClient) -> None:
        wf_id = await _create_workflow(client)

        response = await client.post(f"/api/v1/workflows/{wf_id}/run")

        assert response.status_code == 202
        body = response.json()
        assert "operation_id" in body
        assert "run_id" in body
        assert isinstance(body["run_id"], str)

    async def test_run_pushes_run_accepted_to_sse_queue(
        self, client: httpx.AsyncClient
    ) -> None:
        """run_accepted lands on the SSE queue before launch_background runs.

        The bg task is stubbed by the autouse fixture, so any event in the
        queue must have been pushed synchronously by the route handler. This
        guards the cold-start UX promise: SSE consumers see activity within
        ~50 ms of the POST even when Prefect is warming up.
        """
        wf_id = await _create_workflow(client)

        response = await client.post(f"/api/v1/workflows/{wf_id}/run")
        assert response.status_code == 202
        body = response.json()
        operation_id = body["operation_id"]
        run_id = body["run_id"]

        queue = await get_operation_registry().get_queue(operation_id)
        assert queue is not None, "registry should have a queue for the operation"

        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert isinstance(event, dict)
        assert event["id"] == "evt_accept"
        assert event["event"] == "run_accepted"
        data = event["data"]
        assert isinstance(data, dict)
        assert data["operation_id"] == operation_id
        assert data["run_id"] == run_id
        # workflow_id is the resolved UUID, not the slug used in the URL.
        assert isinstance(data["workflow_id"], str)
        assert len(data["workflow_id"]) > 0
        assert isinstance(data["task_count"], int)
        assert data["task_count"] >= 0
        assert isinstance(data["accepted_at"], str)

    async def test_run_nonexistent_workflow_404(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.post(f"/api/v1/workflows/{nonexistent_id()}/run")

        assert response.status_code == 404

    async def test_run_creates_pending_run_in_db(
        self, client: httpx.AsyncClient
    ) -> None:
        wf_id = await _create_workflow(client)

        run_resp = await client.post(f"/api/v1/workflows/{wf_id}/run")
        run_id = run_resp.json()["run_id"]

        # Verify run exists via GET
        detail_resp = await client.get(f"/api/v1/workflows/{wf_id}/runs/{run_id}")
        assert detail_resp.status_code == 200
        body = detail_resp.json()
        assert body["id"] == run_id
        assert body["status"] == "pending"

    async def test_run_returns_409_when_already_running(
        self, client: httpx.AsyncClient, monkeypatch
    ) -> None:
        wf_id = await _create_workflow(client)

        # Stub the background launcher to a no-op so the first run's PENDING row
        # stays active (never transitions to terminal and frees the slot). The
        # DB-backed guard (uq_workflow_runs_active) is what rejects the second
        # POST — no need to fake an in-process flag.
        monkeypatch.setattr(
            "src.interface.api.services.workflow_execution.launch_background",
            lambda *args, **kwargs: None,
        )

        # First run succeeds (202) and leaves an active PENDING row
        first = await client.post(f"/api/v1/workflows/{wf_id}/run")
        assert first.status_code == 202

        # Second attempt collides on the active-run index → 409
        second = await client.post(f"/api/v1/workflows/{wf_id}/run")
        assert second.status_code == 409
        body = second.json()
        assert body["error"]["code"] == "WORKFLOW_RUNNING"
        assert body["error"]["details"]["workflow_id"] == str(wf_id)


class TestListWorkflowRuns:
    """GET /workflows/{id}/runs — paginated run list."""

    async def test_empty_runs(self, client: httpx.AsyncClient) -> None:
        wf_id = await _create_workflow(client)

        response = await client.get(f"/api/v1/workflows/{wf_id}/runs")

        assert response.status_code == 200
        body = response.json()
        assert body["data"] == []
        assert body["total"] == 0

    async def test_lists_runs_after_execution(self, client: httpx.AsyncClient) -> None:
        wf_id = await _create_workflow(client)
        # A workflow accumulates many *completed* runs over time. Only one
        # active run is allowed at a time (uq_workflow_runs_active), so build
        # history with terminal runs rather than stacking pending POSTs.
        await _seed_completed_runs(wf_id, 2)

        response = await client.get(f"/api/v1/workflows/{wf_id}/runs")

        body = response.json()
        assert body["total"] >= 2
        assert len(body["data"]) >= 2

    async def test_pagination(self, client: httpx.AsyncClient) -> None:
        wf_id = await _create_workflow(client)
        await _seed_completed_runs(wf_id, 3)

        response = await client.get(f"/api/v1/workflows/{wf_id}/runs?limit=2&offset=0")

        body = response.json()
        assert body["total"] >= 3
        assert len(body["data"]) == 2

    async def test_nonexistent_workflow_404(self, client: httpx.AsyncClient) -> None:
        response = await client.get(f"/api/v1/workflows/{nonexistent_id()}/runs")

        assert response.status_code == 404


class TestOperationSnapshotEndpoint:
    """GET /operations/{operation_id}/snapshot — REST fallback for SSE stalls."""

    async def test_snapshot_returns_run_state(self, client: httpx.AsyncClient) -> None:
        """A POST /run lets us fetch the snapshot for that operation_id."""
        wf_id = await _create_workflow(client)

        run_resp = await client.post(f"/api/v1/workflows/{wf_id}/run")
        body = run_resp.json()
        operation_id = body["operation_id"]
        run_id = body["run_id"]

        snap_resp = await client.get(f"/api/v1/operations/{operation_id}/snapshot")
        assert snap_resp.status_code == 200
        snap = snap_resp.json()

        assert snap["operation_id"] == operation_id
        assert snap["id"] == run_id
        assert snap["status"] == "pending"
        assert "nodes" in snap
        assert isinstance(snap["nodes"], list)
        # Pre-created node records exist for every task in the definition.
        assert len(snap["nodes"]) >= 1

    async def test_snapshot_404_for_unknown_operation(
        self, client: httpx.AsyncClient
    ) -> None:
        from uuid import uuid4

        response = await client.get(f"/api/v1/operations/{uuid4()}/snapshot")

        assert response.status_code == 404


class TestGetWorkflowRun:
    """GET /workflows/{id}/runs/{run_id} — run detail with nodes."""

    async def test_returns_run_with_nodes(self, client: httpx.AsyncClient) -> None:
        wf_id = await _create_workflow(client)
        run_resp = await client.post(f"/api/v1/workflows/{wf_id}/run")
        run_id = run_resp.json()["run_id"]

        response = await client.get(f"/api/v1/workflows/{wf_id}/runs/{run_id}")

        assert response.status_code == 200
        body = response.json()
        assert body["id"] == run_id
        assert body["workflow_id"] == wf_id
        assert body["status"] == "pending"
        assert "nodes" in body
        assert len(body["nodes"]) == 1  # 1 task in definition
        assert body["nodes"][0]["node_id"] == "source"
        assert body["nodes"][0]["status"] == "pending"
        assert "definition_snapshot" in body

    async def test_nonexistent_run_404(self, client: httpx.AsyncClient) -> None:
        wf_id = await _create_workflow(client)

        response = await client.get(
            f"/api/v1/workflows/{wf_id}/runs/{nonexistent_id()}"
        )

        assert response.status_code == 404

    async def test_run_wrong_workflow_404(self, client: httpx.AsyncClient) -> None:
        """Accessing run from workflow A via workflow B's URL returns 404."""
        wf_id_a = await _create_workflow(client)
        wf_id_b = await _create_workflow(client)

        run_resp = await client.post(f"/api/v1/workflows/{wf_id_a}/run")
        run_id = run_resp.json()["run_id"]

        # Try to access run_id via workflow B
        response = await client.get(f"/api/v1/workflows/{wf_id_b}/runs/{run_id}")

        assert response.status_code == 404


class TestRunDefinitionVersion:
    """definition_version in run API responses."""

    async def test_run_detail_has_definition_version(
        self, client: httpx.AsyncClient
    ) -> None:
        wf_id = await _create_workflow(client)
        run_resp = await client.post(f"/api/v1/workflows/{wf_id}/run")
        run_id = run_resp.json()["run_id"]

        response = await client.get(f"/api/v1/workflows/{wf_id}/runs/{run_id}")

        assert response.status_code == 200
        assert response.json()["definition_version"] == 1

    async def test_run_list_has_definition_version(
        self, client: httpx.AsyncClient
    ) -> None:
        wf_id = await _create_workflow(client)
        await client.post(f"/api/v1/workflows/{wf_id}/run")

        response = await client.get(f"/api/v1/workflows/{wf_id}/runs")

        body = response.json()
        assert body["total"] >= 1
        assert body["data"][0]["definition_version"] == 1


class TestWorkflowListIncludesLastRun:
    """GET /workflows includes last_run field when runs exist."""

    async def test_list_shows_last_run(self, client: httpx.AsyncClient) -> None:
        wf_id = await _create_workflow(client)
        await client.post(f"/api/v1/workflows/{wf_id}/run")

        response = await client.get("/api/v1/workflows")
        body = response.json()

        # Find our workflow in the list
        our_wf = next((w for w in body["data"] if w["id"] == wf_id), None)
        assert our_wf is not None
        assert our_wf["last_run"] is not None
        assert our_wf["last_run"]["status"] == "pending"


class TestListActiveRuns:
    """GET /workflows/active-runs — cross-workflow in-flight runs for the user."""

    async def test_empty_when_nothing_running(self, client: httpx.AsyncClient) -> None:
        await _create_workflow(client)

        response = await client.get("/api/v1/workflows/active-runs")

        assert response.status_code == 200
        body = response.json()
        assert body["data"] == []
        assert body["total"] == 0

    async def test_lists_active_run_with_operation_id(
        self, client: httpx.AsyncClient
    ) -> None:
        wf_id = await _create_workflow(client)
        run_resp = await client.post(f"/api/v1/workflows/{wf_id}/run")
        operation_id = run_resp.json()["operation_id"]

        response = await client.get("/api/v1/workflows/active-runs")

        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        active = body["data"][0]
        assert active["workflow_id"] == wf_id
        assert active["status"] == "pending"
        # operation_id is the field that lets the client reconnect (snapshot/SSE).
        assert active["operation_id"] == operation_id

    async def test_excludes_completed_runs(self, client: httpx.AsyncClient) -> None:
        wf_id = await _create_workflow(client)
        await _seed_completed_runs(wf_id, 1)

        response = await client.get("/api/v1/workflows/active-runs")

        assert response.status_code == 200
        assert response.json()["data"] == []
