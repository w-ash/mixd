"""Integration tests for workflow version API endpoints.

Tests GET /workflows/{id}/versions (list), GET /workflows/{id}/versions/{version}
(single), and POST /workflows/{id}/versions/{version}/revert (restore).
Version records are created automatically when a workflow's task pipeline changes.
"""

import httpx
import pytest

import src.interface.api.routes.workflows as _workflows_mod
from tests.fixtures.factories import nonexistent_id
from tests.integration.api.conftest import (
    create_workflow as _create_workflow,
    valid_workflow_definition as _valid_definition,
)


def _two_task_definition() -> dict:
    """Definition with two tasks — structurally different from _valid_definition."""
    return {
        "id": "test-wf",
        "name": "Test Workflow v2",
        "description": "Updated with filter",
        "version": "1.0",
        "tasks": [
            {
                "id": "source",
                "type": "source.liked_tracks",
                "config": {"service": "spotify"},
                "upstream": [],
            },
            {
                "id": "dedup",
                "type": "filter.deduplicate",
                "config": {},
                "upstream": ["source"],
            },
        ],
    }


@pytest.fixture(autouse=True)
def _stub_workflow_background(monkeypatch):
    """Prevent background workflow execution in tests — only verify endpoints."""

    def _noop_launch(_name: str, _coro_factory: object, **_kwargs: object) -> None:
        pass

    monkeypatch.setattr(_workflows_mod, "launch_background", _noop_launch)


async def _update_workflow_tasks(
    client: httpx.AsyncClient, wf_id: str, definition: dict
) -> None:
    """Helper: update a workflow with a new definition (task pipeline change)."""
    resp = await client.patch(
        f"/api/v1/workflows/{wf_id}", json={"definition": definition}
    )
    assert resp.status_code == 200


class TestListWorkflowVersions:
    """GET /workflows/{id}/versions — list version history."""

    async def test_empty_versions_initially(self, client: httpx.AsyncClient) -> None:
        """A new workflow has no version history (versions are created on update)."""
        wf_id = await _create_workflow(client)

        response = await client.get(f"/api/v1/workflows/{wf_id}/versions")

        assert response.status_code == 200
        body = response.json()
        assert isinstance(body, list)
        assert len(body) == 0

    async def test_versions_after_task_update(self, client: httpx.AsyncClient) -> None:
        """Updating the task pipeline creates a version record."""
        wf_id = await _create_workflow(client)

        # Update with a different task pipeline
        await _update_workflow_tasks(client, wf_id, _two_task_definition())

        response = await client.get(f"/api/v1/workflows/{wf_id}/versions")

        assert response.status_code == 200
        versions = response.json()
        assert len(versions) == 1

        v = versions[0]
        assert v["workflow_id"] == wf_id
        assert v["version"] == 1
        assert "definition" in v
        assert "created_at" in v
        # The snapshot is the PREVIOUS definition (before the update)
        assert len(v["definition"]["tasks"]) == 1

    async def test_multiple_updates_create_multiple_versions(
        self, client: httpx.AsyncClient
    ) -> None:
        """Each task pipeline change creates a new version record."""
        wf_id = await _create_workflow(client)

        # First update: 1 task -> 2 tasks
        await _update_workflow_tasks(client, wf_id, _two_task_definition())

        # Second update: 2 tasks -> back to 1 task
        await _update_workflow_tasks(client, wf_id, _valid_definition())

        response = await client.get(f"/api/v1/workflows/{wf_id}/versions")

        versions = response.json()
        assert len(versions) == 2
        # Versions should be numbered 1 and 2
        version_nums = sorted(v["version"] for v in versions)
        assert version_nums == [1, 2]

    async def test_nonexistent_workflow_returns_404(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get(f"/api/v1/workflows/{nonexistent_id()}/versions")

        assert response.status_code == 404

    async def test_name_only_update_does_not_create_version(
        self, client: httpx.AsyncClient
    ) -> None:
        """Changing only name/description (same tasks) does not create a version."""
        wf_id = await _create_workflow(client)

        name_only = _valid_definition()
        name_only["name"] = "Renamed Workflow"
        name_only["description"] = "New description"
        await _update_workflow_tasks(client, wf_id, name_only)

        response = await client.get(f"/api/v1/workflows/{wf_id}/versions")

        assert response.status_code == 200
        assert len(response.json()) == 0


class TestGetWorkflowVersion:
    """GET /workflows/{id}/versions/{version} — get specific version."""

    async def test_get_specific_version(self, client: httpx.AsyncClient) -> None:
        wf_id = await _create_workflow(client)
        await _update_workflow_tasks(client, wf_id, _two_task_definition())

        response = await client.get(f"/api/v1/workflows/{wf_id}/versions/1")

        assert response.status_code == 200
        body = response.json()
        assert body["workflow_id"] == wf_id
        assert body["version"] == 1
        assert "definition" in body
        # Version 1 is a snapshot of the original definition (1 task)
        assert len(body["definition"]["tasks"]) == 1

    async def test_version_has_change_summary(self, client: httpx.AsyncClient) -> None:
        """Version records include a change summary describing what changed."""
        wf_id = await _create_workflow(client)
        await _update_workflow_tasks(client, wf_id, _two_task_definition())

        response = await client.get(f"/api/v1/workflows/{wf_id}/versions/1")

        body = response.json()
        # change_summary may be None or a descriptive string
        assert "change_summary" in body

    async def test_nonexistent_version_returns_404(
        self, client: httpx.AsyncClient
    ) -> None:
        wf_id = await _create_workflow(client)

        response = await client.get(f"/api/v1/workflows/{wf_id}/versions/999")

        assert response.status_code == 404

    async def test_nonexistent_workflow_returns_404(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get(f"/api/v1/workflows/{nonexistent_id()}/versions/1")

        assert response.status_code == 404


class TestRevertWorkflowVersion:
    """POST /workflows/{id}/versions/{version}/revert — restore previous definition."""

    async def test_revert_restores_definition(self, client: httpx.AsyncClient) -> None:
        """Reverting to version 1 restores the original definition."""
        wf_id = await _create_workflow(client)

        # Update: original 1-task -> 2-task (creates version 1)
        await _update_workflow_tasks(client, wf_id, _two_task_definition())

        # Verify current state has 2 tasks
        detail = await client.get(f"/api/v1/workflows/{wf_id}")
        assert len(detail.json()["definition"]["tasks"]) == 2

        # Revert to version 1 (the original 1-task definition)
        response = await client.post(f"/api/v1/workflows/{wf_id}/versions/1/revert")

        assert response.status_code == 200
        body = response.json()
        assert len(body["definition"]["tasks"]) == 1
        assert body["definition"]["tasks"][0]["id"] == "source"

    async def test_revert_bumps_definition_version(
        self, client: httpx.AsyncClient
    ) -> None:
        """Reverting increments the workflow's definition_version."""
        wf_id = await _create_workflow(client)

        # Update: creates version record 1
        await _update_workflow_tasks(client, wf_id, _two_task_definition())

        detail = await client.get(f"/api/v1/workflows/{wf_id}")
        version_before_revert = detail.json()["definition_version"]

        # Revert to version 1
        resp = await client.post(f"/api/v1/workflows/{wf_id}/versions/1/revert")

        assert resp.status_code == 200
        # Revert should bump definition_version by 1
        assert resp.json()["definition_version"] == version_before_revert + 1

    async def test_revert_creates_snapshot_of_current(
        self, client: httpx.AsyncClient
    ) -> None:
        """Reverting snapshots the current definition before restoring."""
        wf_id = await _create_workflow(client)

        # Update: 1-task -> 2-task (creates version 1)
        await _update_workflow_tasks(client, wf_id, _two_task_definition())

        # Revert to version 1
        await client.post(f"/api/v1/workflows/{wf_id}/versions/1/revert")

        # Now there should be 2 version records: original snapshot + pre-revert snapshot
        versions_resp = await client.get(f"/api/v1/workflows/{wf_id}/versions")
        versions = versions_resp.json()
        assert len(versions) == 2

        # The second snapshot should be the 2-task definition (saved before revert)
        v2 = next(v for v in versions if v["version"] == 2)
        assert len(v2["definition"]["tasks"]) == 2
        assert "Before revert" in (v2["change_summary"] or "")

    async def test_revert_nonexistent_version_returns_404(
        self, client: httpx.AsyncClient
    ) -> None:
        wf_id = await _create_workflow(client)

        response = await client.post(f"/api/v1/workflows/{wf_id}/versions/999/revert")

        assert response.status_code == 404

    async def test_revert_nonexistent_workflow_returns_404(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.post(
            f"/api/v1/workflows/{nonexistent_id()}/versions/1/revert"
        )

        assert response.status_code == 404
