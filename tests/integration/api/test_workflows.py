"""Integration tests for workflow API endpoints.

Tests the full request -> route -> use case -> database -> response cycle.
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


class TestListWorkflows:
    async def test_empty_list(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/api/v1/workflows")

        assert response.status_code == 200
        body = response.json()
        # Templates may be seeded by lifespan, so just verify structure
        assert "data" in body
        assert "total" in body
        assert isinstance(body["data"], list)

    async def test_list_after_create(self, client: httpx.AsyncClient) -> None:
        await client.post("/api/v1/workflows", json={"definition": _valid_definition()})

        response = await client.get("/api/v1/workflows")
        body = response.json()
        assert body["total"] >= 1


class TestCreateWorkflow:
    async def test_create_valid(self, client: httpx.AsyncClient) -> None:
        response = await client.post(
            "/api/v1/workflows", json={"definition": _valid_definition()}
        )

        assert response.status_code == 201
        body = response.json()
        assert body["name"] == "Test Workflow"
        assert body["task_count"] == 1
        assert "definition" in body
        assert "id" in body

    async def test_create_invalid_empty_tasks(self, client: httpx.AsyncClient) -> None:
        definition = _valid_definition()
        definition["tasks"] = []

        response = await client.post(
            "/api/v1/workflows", json={"definition": definition}
        )

        assert response.status_code == 400

    async def test_create_missing_definition(self, client: httpx.AsyncClient) -> None:
        response = await client.post("/api/v1/workflows", json={})

        assert response.status_code == 422


class TestGetWorkflow:
    async def test_get_existing(self, client: httpx.AsyncClient) -> None:
        create_resp = await client.post(
            "/api/v1/workflows", json={"definition": _valid_definition()}
        )
        wf_id = create_resp.json()["id"]

        response = await client.get(f"/api/v1/workflows/{wf_id}")

        assert response.status_code == 200
        body = response.json()
        assert body["id"] == wf_id
        assert "definition" in body

    async def test_get_nonexistent(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/api/v1/workflows/99999")

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    async def test_detail_includes_last_run(
        self, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """GET /workflows/{id} should include last_run when runs exist."""
        monkeypatch.setattr(
            _workflows_mod, "launch_background", lambda *a, **kw: None
        )

        create_resp = await client.post(
            "/api/v1/workflows", json={"definition": _valid_definition()}
        )
        wf_id = create_resp.json()["id"]

        # Trigger a run (background execution is stubbed)
        run_resp = await client.post(f"/api/v1/workflows/{wf_id}/run")
        assert run_resp.status_code == 202
        run_id = run_resp.json()["run_id"]

        # GET detail should include last_run
        detail_resp = await client.get(f"/api/v1/workflows/{wf_id}")
        assert detail_resp.status_code == 200
        body = detail_resp.json()
        assert body["last_run"] is not None
        assert body["last_run"]["id"] == run_id
        assert body["last_run"]["status"] == "pending"


class TestUpdateWorkflow:
    async def test_update_user_workflow(self, client: httpx.AsyncClient) -> None:
        create_resp = await client.post(
            "/api/v1/workflows", json={"definition": _valid_definition()}
        )
        wf_id = create_resp.json()["id"]

        updated_def = _valid_definition()
        updated_def["name"] = "Updated Name"

        response = await client.patch(
            f"/api/v1/workflows/{wf_id}", json={"definition": updated_def}
        )

        assert response.status_code == 200
        assert response.json()["name"] == "Updated Name"

    async def test_update_nonexistent(self, client: httpx.AsyncClient) -> None:
        response = await client.patch(
            "/api/v1/workflows/99999",
            json={"definition": _valid_definition()},
        )

        assert response.status_code == 404


class TestDeleteWorkflow:
    async def test_delete_user_workflow(self, client: httpx.AsyncClient) -> None:
        create_resp = await client.post(
            "/api/v1/workflows", json={"definition": _valid_definition()}
        )
        wf_id = create_resp.json()["id"]

        response = await client.delete(f"/api/v1/workflows/{wf_id}")

        assert response.status_code == 204

        get_resp = await client.get(f"/api/v1/workflows/{wf_id}")
        assert get_resp.status_code == 404

    async def test_delete_nonexistent(self, client: httpx.AsyncClient) -> None:
        response = await client.delete("/api/v1/workflows/99999")

        assert response.status_code == 404


class TestValidateWorkflow:
    async def test_valid_workflow(self, client: httpx.AsyncClient) -> None:
        response = await client.post(
            "/api/v1/workflows/validate",
            json={"definition": _valid_definition()},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["valid"] is True
        assert body["errors"] == []

    async def test_invalid_empty_tasks(self, client: httpx.AsyncClient) -> None:
        definition = _valid_definition()
        definition["tasks"] = []

        response = await client.post(
            "/api/v1/workflows/validate",
            json={"definition": definition},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["valid"] is False
        assert len(body["errors"]) >= 1


class TestDefinitionVersion:
    """definition_version exposed in workflow and run API responses."""

    async def test_new_workflow_has_version_1(self, client: httpx.AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/workflows", json={"definition": _valid_definition()}
        )

        assert resp.status_code == 201
        assert resp.json()["definition_version"] == 1

    async def test_version_in_get_detail(self, client: httpx.AsyncClient) -> None:
        create_resp = await client.post(
            "/api/v1/workflows", json={"definition": _valid_definition()}
        )
        wf_id = create_resp.json()["id"]

        resp = await client.get(f"/api/v1/workflows/{wf_id}")
        assert resp.json()["definition_version"] == 1

    async def test_version_in_list(self, client: httpx.AsyncClient) -> None:
        await client.post("/api/v1/workflows", json={"definition": _valid_definition()})

        resp = await client.get("/api/v1/workflows")
        workflows = resp.json()["data"]
        # At least one workflow has definition_version
        assert any(w.get("definition_version") is not None for w in workflows)


class TestListNodeTypes:
    async def test_returns_node_list(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/api/v1/workflows/nodes")

        assert response.status_code == 200
        body = response.json()
        assert isinstance(body, list)
        assert len(body) > 0

        # Verify structure of first node
        node = body[0]
        assert "type" in node
        assert "category" in node
        assert "description" in node
