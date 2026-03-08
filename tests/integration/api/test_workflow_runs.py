"""Integration tests for workflow run API endpoints.

Tests POST /run (202, 409, 404), GET /runs (pagination),
and GET /runs/{run_id} (200, 404, with nodes).
"""

import httpx
import pytest

import src.interface.api.routes.workflows as _workflows_mod


def _valid_definition() -> dict:
    """Minimal valid workflow definition for API requests."""
    return {
        "id": "test-wf",
        "name": "Test Workflow",
        "description": "A test",
        "version": "1.0",
        "tasks": [
            {
                "id": "source",
                "type": "source.liked_tracks",
                "config": {"service": "spotify"},
                "upstream": [],
            }
        ],
    }


@pytest.fixture(autouse=True)
def _stub_workflow_background(monkeypatch):
    """Prevent background workflow execution in tests — only verify endpoints."""

    def _noop_launch(_name: str, _coro_factory: object, **_kwargs: object) -> None:
        pass

    monkeypatch.setattr(_workflows_mod, "launch_background", _noop_launch)


async def _create_workflow(client: httpx.AsyncClient) -> int:
    """Helper: create a workflow and return its ID."""
    resp = await client.post(
        "/api/v1/workflows", json={"definition": _valid_definition()}
    )
    assert resp.status_code == 201
    return resp.json()["id"]


class TestRunWorkflowEndpoint:
    """POST /workflows/{id}/run — starts execution."""

    async def test_run_returns_202(self, client: httpx.AsyncClient) -> None:
        wf_id = await _create_workflow(client)

        response = await client.post(f"/api/v1/workflows/{wf_id}/run")

        assert response.status_code == 202
        body = response.json()
        assert "operation_id" in body
        assert "run_id" in body
        assert isinstance(body["run_id"], int)

    async def test_run_nonexistent_workflow_404(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.post("/api/v1/workflows/99999/run")

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

        # First run succeeds (202)
        first = await client.post(f"/api/v1/workflows/{wf_id}/run")
        assert first.status_code == 202

        # Monkeypatch the execution guard to report workflow as running
        async def _always_running(_wf_id: str) -> bool:
            return True

        monkeypatch.setattr(
            "src.application.use_cases.workflow_runs.is_workflow_running",
            _always_running,
        )

        # Second attempt returns 409
        second = await client.post(f"/api/v1/workflows/{wf_id}/run")
        assert second.status_code == 409
        body = second.json()
        assert body["error"]["code"] == "WORKFLOW_RUNNING"


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
        await client.post(f"/api/v1/workflows/{wf_id}/run")
        await client.post(f"/api/v1/workflows/{wf_id}/run")

        response = await client.get(f"/api/v1/workflows/{wf_id}/runs")

        body = response.json()
        assert body["total"] >= 2
        assert len(body["data"]) >= 2

    async def test_pagination(self, client: httpx.AsyncClient) -> None:
        wf_id = await _create_workflow(client)
        # Create 3 runs
        for _ in range(3):
            await client.post(f"/api/v1/workflows/{wf_id}/run")

        response = await client.get(f"/api/v1/workflows/{wf_id}/runs?limit=2&offset=0")

        body = response.json()
        assert body["total"] >= 3
        assert len(body["data"]) == 2

    async def test_nonexistent_workflow_404(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/api/v1/workflows/99999/runs")

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

        response = await client.get(f"/api/v1/workflows/{wf_id}/runs/99999")

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
