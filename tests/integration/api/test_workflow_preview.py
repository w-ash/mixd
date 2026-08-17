"""Integration tests for workflow preview API endpoints.

Tests POST /workflows/preview (unsaved) and POST /workflows/{id}/preview (saved).
Preview endpoints launch background tasks (stubbed in tests) and return 202
with an operation_id for SSE streaming.

Previews write canonical tracks, so the kickoff guards them: 409 while a run
of the same workflow is active, 429 at operation-slot capacity.
"""

import httpx2
import pytest

from src.config.constants import SSEConstants
import src.interface.api.routes.workflows as _workflows_mod
import src.interface.api.services.sse_operations as _sse_ops
import src.interface.api.services.workflow_execution as _wf_exec_mod
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
    # The run endpoint's launcher lives in workflow_execution; stubbing it too
    # lets a test create a PENDING run row without executing anything.
    monkeypatch.setattr(_wf_exec_mod, "launch_background", _noop_launch)


@pytest.fixture(autouse=True)
def _release_leaked_slots():
    """Drop slot claims the stubbed background task never releases —
    otherwise three previews exhaust the cap and later kickoffs 429."""
    before = set(_sse_ops._active_operations)
    yield
    _sse_ops._active_operations.intersection_update(before)


class TestPreviewUnsavedWorkflow:
    """POST /workflows/preview — preview an unsaved definition."""

    async def test_returns_202_with_operation_id(
        self, client: httpx2.AsyncClient
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
        self, client: httpx2.AsyncClient
    ) -> None:
        """Missing required 'definition' field returns 422 validation error."""
        response = await client.post("/api/v1/workflows/preview", json={})

        assert response.status_code == 422

    async def test_empty_tasks_still_returns_202(
        self, client: httpx2.AsyncClient
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
        self, client: httpx2.AsyncClient
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
        self, client: httpx2.AsyncClient
    ) -> None:
        wf_id = await _create_workflow(client)

        response = await client.post(f"/api/v1/workflows/{wf_id}/preview")

        assert response.status_code == 202
        body = response.json()
        assert "operation_id" in body
        assert isinstance(body["operation_id"], str)
        assert len(body["operation_id"]) > 0

    async def test_nonexistent_workflow_returns_404(
        self, client: httpx2.AsyncClient
    ) -> None:
        response = await client.post(f"/api/v1/workflows/{nonexistent_id()}/preview")

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    async def test_preview_after_update_uses_latest_definition(
        self, client: httpx2.AsyncClient
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


class TestPreviewGuards:
    """Previews are canonical-track writers; the kickoff guards them like runs."""

    async def test_preview_of_workflow_with_active_run_returns_409(
        self, client: httpx2.AsyncClient
    ) -> None:
        """The run row is PENDING (its task is stubbed); the preview kickoff
        refuses to add a second writer on the same workflow's sources."""
        wf_id = await _create_workflow(client)
        run_resp = await client.post(f"/api/v1/workflows/{wf_id}/run")
        assert run_resp.status_code == 202

        response = await client.post(f"/api/v1/workflows/{wf_id}/preview")

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "WORKFLOW_RUNNING"

    async def test_unsaved_preview_has_no_run_to_collide_with(
        self, client: httpx2.AsyncClient
    ) -> None:
        """An unsaved definition has no workflow row, hence no run guard."""
        wf_id = await _create_workflow(client)
        run_resp = await client.post(f"/api/v1/workflows/{wf_id}/run")
        assert run_resp.status_code == 202

        response = await client.post(
            "/api/v1/workflows/preview",
            json={"definition": _valid_definition()},
        )

        assert response.status_code == 202

    async def test_preview_counts_against_the_operation_cap(
        self, client: httpx2.AsyncClient
    ) -> None:
        """Previews used to bypass the global concurrency cap entirely."""
        tokens = [
            f"cap-filler-{i}" for i in range(SSEConstants.MAX_CONCURRENT_OPERATIONS)
        ]
        for token in tokens:
            _sse_ops.acquire_operation_slot(token)
        try:
            response = await client.post(
                "/api/v1/workflows/preview",
                json={"definition": _valid_definition()},
            )
            assert response.status_code == 429
        finally:
            for token in tokens:
                _sse_ops.release_operation_slot(token)

    async def test_preview_claims_a_slot_until_its_task_releases_it(
        self, client: httpx2.AsyncClient
    ) -> None:
        """Kickoff acquires; ``execute_preview_background`` releases (stubbed
        here, so the claim is still visible after the 202)."""
        before = len(_sse_ops._active_operations)

        response = await client.post(
            "/api/v1/workflows/preview",
            json={"definition": _valid_definition()},
        )

        assert response.status_code == 202
        operation_id = response.json()["operation_id"]
        assert operation_id in _sse_ops._active_operations
        assert len(_sse_ops._active_operations) == before + 1
