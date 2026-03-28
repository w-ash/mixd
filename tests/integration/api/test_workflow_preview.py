"""Integration tests for workflow preview API endpoints.

Tests POST /workflows/preview (unsaved) and POST /workflows/{id}/preview (saved).
Preview endpoints launch background tasks (stubbed in tests) and return 202
with an operation_id for SSE streaming.
"""

import httpx
import pytest

import src.interface.api.routes.workflows as _workflows_mod
from tests.fixtures.factories import nonexistent_id
from tests.integration.api.conftest import (
    create_workflow as _create_workflow,
    valid_workflow_definition as _valid_definition,
)


@pytest.fixture(autouse=True)
def _stub_workflow_background(monkeypatch):
    """Prevent background workflow execution in tests — only verify endpoints."""

    def _noop_launch(_name: str, _coro_factory: object, **_kwargs: object) -> None:
        pass

    monkeypatch.setattr(_workflows_mod, "launch_background", _noop_launch)


class TestPreviewUnsavedWorkflow:
    """POST /workflows/preview — preview an unsaved definition."""

    async def test_returns_202_with_operation_id(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.post(
            "/api/v1/workflows/preview",
            json={"definition": _valid_definition()},
        )

        assert response.status_code == 202
        body = response.json()
        assert "operation_id" in body
        assert isinstance(body["operation_id"], str)
        assert len(body["operation_id"]) > 0

    async def test_invalid_definition_returns_422(
        self, client: httpx.AsyncClient
    ) -> None:
        """Missing required 'definition' field returns 422 validation error."""
        response = await client.post("/api/v1/workflows/preview", json={})

        assert response.status_code == 422

    async def test_empty_tasks_still_returns_202(
        self, client: httpx.AsyncClient
    ) -> None:
        """Empty tasks are accepted synchronously — validation runs in background."""
        definition = _valid_definition()
        definition["tasks"] = []

        response = await client.post(
            "/api/v1/workflows/preview",
            json={"definition": definition},
        )

        # Preview does NOT validate up-front; errors arrive via SSE
        assert response.status_code == 202
        assert "operation_id" in response.json()

    async def test_each_preview_gets_unique_operation_id(
        self, client: httpx.AsyncClient
    ) -> None:
        """Multiple preview requests produce distinct operation IDs."""
        resp1 = await client.post(
            "/api/v1/workflows/preview",
            json={"definition": _valid_definition()},
        )
        resp2 = await client.post(
            "/api/v1/workflows/preview",
            json={"definition": _valid_definition()},
        )

        assert resp1.status_code == 202
        assert resp2.status_code == 202
        assert resp1.json()["operation_id"] != resp2.json()["operation_id"]


class TestPreviewSavedWorkflow:
    """POST /workflows/{id}/preview — preview a saved workflow."""

    async def test_returns_202_with_operation_id(
        self, client: httpx.AsyncClient
    ) -> None:
        wf_id = await _create_workflow(client)

        response = await client.post(f"/api/v1/workflows/{wf_id}/preview")

        assert response.status_code == 202
        body = response.json()
        assert "operation_id" in body
        assert isinstance(body["operation_id"], str)
        assert len(body["operation_id"]) > 0

    async def test_nonexistent_workflow_returns_404(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.post(f"/api/v1/workflows/{nonexistent_id()}/preview")

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    async def test_preview_after_update_uses_latest_definition(
        self, client: httpx.AsyncClient
    ) -> None:
        """Preview of a saved workflow should succeed even after updates."""
        wf_id = await _create_workflow(client)

        # Update the workflow
        updated_def = _valid_definition()
        updated_def["name"] = "Updated Workflow"
        await client.patch(
            f"/api/v1/workflows/{wf_id}", json={"definition": updated_def}
        )

        # Preview should still work
        response = await client.post(f"/api/v1/workflows/{wf_id}/preview")
        assert response.status_code == 202
