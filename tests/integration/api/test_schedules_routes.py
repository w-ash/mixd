"""Integration tests for the schedule HTTP endpoints (v0.8.3).

Full stack — routes → use cases → real repos → PostgreSQL. Covers the workflow
schedule routes (on the workflows router), the sync schedule router, the global
list, and the status-code contract (201 vs 200 on PUT, 404 on absent/cross-target,
400 on a bad sync target, 422 on an inconsistent cadence payload).
"""

import httpx
import pytest

from tests.fixtures.factories import nonexistent_id
from tests.integration.api.conftest import create_workflow as _create_workflow

pytestmark = pytest.mark.integration

_DAILY = {"schedule_type": "daily", "hour": 6, "minute": 30, "timezone": "UTC"}
_WEEKLY = {
    "schedule_type": "weekly",
    "hour": 6,
    "minute": 30,
    "day_of_week": 0,
    "timezone": "UTC",
}


class TestWorkflowSchedule:
    async def test_put_creates_then_replaces(self, client: httpx.AsyncClient) -> None:
        wf_id = await _create_workflow(client)

        created = await client.put(f"/api/v1/workflows/{wf_id}/schedule", json=_DAILY)
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["schedule_type"] == "daily"
        assert body["target_type"] == "workflow"
        assert body["status"] == "enabled"
        assert body["next_run_at"] is not None

        # Same target again → replace, 200 (not a 409 conflict).
        replaced = await client.put(f"/api/v1/workflows/{wf_id}/schedule", json=_WEEKLY)
        assert replaced.status_code == 200, replaced.text
        assert replaced.json()["schedule_type"] == "weekly"
        assert replaced.json()["day_of_week"] == 0

    async def test_get_returns_404_when_absent(self, client: httpx.AsyncClient) -> None:
        wf_id = await _create_workflow(client)
        resp = await client.get(f"/api/v1/workflows/{wf_id}/schedule")
        assert resp.status_code == 404

    async def test_put_unknown_workflow_404(self, client: httpx.AsyncClient) -> None:
        resp = await client.put(
            f"/api/v1/workflows/{nonexistent_id()}/schedule", json=_DAILY
        )
        assert resp.status_code == 404

    async def test_patch_toggles_status(self, client: httpx.AsyncClient) -> None:
        wf_id = await _create_workflow(client)
        await client.put(f"/api/v1/workflows/{wf_id}/schedule", json=_DAILY)

        disabled = await client.patch(
            f"/api/v1/workflows/{wf_id}/schedule", json={"enabled": False}
        )
        assert disabled.status_code == 200
        assert disabled.json()["status"] == "disabled"

    async def test_delete_then_get_404(self, client: httpx.AsyncClient) -> None:
        wf_id = await _create_workflow(client)
        await client.put(f"/api/v1/workflows/{wf_id}/schedule", json=_DAILY)

        deleted = await client.delete(f"/api/v1/workflows/{wf_id}/schedule")
        assert deleted.status_code == 204
        assert (
            await client.get(f"/api/v1/workflows/{wf_id}/schedule")
        ).status_code == 404

    async def test_weekly_without_day_is_422(self, client: httpx.AsyncClient) -> None:
        wf_id = await _create_workflow(client)
        resp = await client.put(
            f"/api/v1/workflows/{wf_id}/schedule",
            json={"schedule_type": "weekly", "hour": 6, "minute": 30},
        )
        assert resp.status_code == 422  # Pydantic model validator

    async def test_invalid_timezone_is_422(self, client: httpx.AsyncClient) -> None:
        # A non-IANA zone is now rejected at the request boundary (422), not as a
        # use-case ValueError surfaced as a generic 400.
        wf_id = await _create_workflow(client)
        resp = await client.put(
            f"/api/v1/workflows/{wf_id}/schedule",
            json={**_DAILY, "timezone": "PST"},
        )
        assert resp.status_code == 422, resp.text


class TestSyncSchedule:
    async def test_put_and_get(self, client: httpx.AsyncClient) -> None:
        created = await client.put("/api/v1/sync/schedules/lastfm:plays", json=_DAILY)
        assert created.status_code == 201, created.text
        assert created.json()["sync_target"] == "lastfm:plays"
        assert created.json()["target_type"] == "sync"

        fetched = await client.get("/api/v1/sync/schedules/lastfm:plays")
        assert fetched.status_code == 200
        assert fetched.json()["sync_target"] == "lastfm:plays"

    async def test_unschedulable_target_400(self, client: httpx.AsyncClient) -> None:
        resp = await client.put("/api/v1/sync/schedules/spotify:plays", json=_DAILY)
        assert resp.status_code == 400  # validate_sync_target → ValueError

    async def test_delete(self, client: httpx.AsyncClient) -> None:
        await client.put("/api/v1/sync/schedules/spotify:likes", json=_DAILY)
        deleted = await client.delete("/api/v1/sync/schedules/spotify:likes")
        assert deleted.status_code == 204


class TestListSchedules:
    async def test_lists_workflow_and_sync(self, client: httpx.AsyncClient) -> None:
        wf_id = await _create_workflow(client)
        await client.put(f"/api/v1/workflows/{wf_id}/schedule", json=_DAILY)
        await client.put("/api/v1/sync/schedules/lastfm:plays", json=_DAILY)

        resp = await client.get("/api/v1/schedules")
        assert resp.status_code == 200
        data = resp.json()["data"]
        target_types = {row["target_type"] for row in data}
        assert target_types == {"workflow", "sync"}
        assert len(data) == 2
