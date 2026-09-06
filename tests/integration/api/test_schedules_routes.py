"""Integration tests for the schedule HTTP endpoints (v0.8.3).

Full stack — routes → use cases → real repos → PostgreSQL. Covers the workflow
schedule routes (on the workflows router), the sync schedule router, the global
list, and the status-code contract (201 vs 200 on PUT, 404 on absent/cross-target,
400 on a bad sync target, 422 on an inconsistent cadence payload).
"""

from collections.abc import Callable, Collection, Mapping

import httpx2
import pytest

from src.application.use_cases._shared.sync_targets import SYNC_TARGETS
from src.domain.repositories.play import RECENTLY_PLAYED_SCOPE
from src.infrastructure.connectors._shared.token_storage import StoredToken
from src.interface.api import deps as deps_module
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
    async def test_put_creates_then_replaces(self, client: httpx2.AsyncClient) -> None:
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

    async def test_get_returns_404_when_absent(
        self, client: httpx2.AsyncClient
    ) -> None:
        wf_id = await _create_workflow(client)
        resp = await client.get(f"/api/v1/workflows/{wf_id}/schedule")
        assert resp.status_code == 404

    async def test_put_unknown_workflow_404(self, client: httpx2.AsyncClient) -> None:
        resp = await client.put(
            f"/api/v1/workflows/{nonexistent_id()}/schedule", json=_DAILY
        )
        assert resp.status_code == 404

    async def test_patch_toggles_status(self, client: httpx2.AsyncClient) -> None:
        wf_id = await _create_workflow(client)
        await client.put(f"/api/v1/workflows/{wf_id}/schedule", json=_DAILY)

        disabled = await client.patch(
            f"/api/v1/workflows/{wf_id}/schedule", json={"enabled": False}
        )
        assert disabled.status_code == 200
        assert disabled.json()["status"] == "disabled"

    async def test_delete_then_get_404(self, client: httpx2.AsyncClient) -> None:
        wf_id = await _create_workflow(client)
        await client.put(f"/api/v1/workflows/{wf_id}/schedule", json=_DAILY)

        deleted = await client.delete(f"/api/v1/workflows/{wf_id}/schedule")
        assert deleted.status_code == 204
        assert (
            await client.get(f"/api/v1/workflows/{wf_id}/schedule")
        ).status_code == 404

    async def test_weekly_without_day_is_422(self, client: httpx2.AsyncClient) -> None:
        wf_id = await _create_workflow(client)
        resp = await client.put(
            f"/api/v1/workflows/{wf_id}/schedule",
            json={"schedule_type": "weekly", "hour": 6, "minute": 30},
        )
        assert resp.status_code == 422  # Pydantic model validator

    async def test_invalid_timezone_is_422(self, client: httpx2.AsyncClient) -> None:
        # A non-IANA zone is now rejected at the request boundary (422), not as a
        # use-case ValueError surfaced as a generic 400.
        wf_id = await _create_workflow(client)
        resp = await client.put(
            f"/api/v1/workflows/{wf_id}/schedule",
            json={**_DAILY, "timezone": "PST"},
        )
        assert resp.status_code == 422, resp.text


class TestSyncSchedule:
    async def test_put_and_get(self, client: httpx2.AsyncClient) -> None:
        created = await client.put("/api/v1/sync/schedules/lastfm:plays", json=_DAILY)
        assert created.status_code == 201, created.text
        assert created.json()["sync_target"] == "lastfm:plays"
        assert created.json()["target_type"] == "sync"

        fetched = await client.get("/api/v1/sync/schedules/lastfm:plays")
        assert fetched.status_code == 200
        assert fetched.json()["sync_target"] == "lastfm:plays"

    async def test_unschedulable_target_400(self, client: httpx2.AsyncClient) -> None:
        resp = await client.put("/api/v1/sync/schedules/spotify:plays", json=_DAILY)
        assert resp.status_code == 400  # validate_sync_target → ValueError

    async def test_delete(self, client: httpx2.AsyncClient) -> None:
        await client.put("/api/v1/sync/schedules/spotify:likes", json=_DAILY)
        deleted = await client.delete("/api/v1/sync/schedules/spotify:likes")
        assert deleted.status_code == 204


class TestListSchedules:
    async def test_lists_workflow_and_sync(self, client: httpx2.AsyncClient) -> None:
        wf_id = await _create_workflow(client)
        await client.put(f"/api/v1/workflows/{wf_id}/schedule", json=_DAILY)
        await client.put("/api/v1/sync/schedules/lastfm:plays", json=_DAILY)

        resp = await client.get("/api/v1/schedules")
        assert resp.status_code == 200
        data = resp.json()["data"]
        target_types = {row["target_type"] for row in data}
        assert target_types == {"workflow", "sync"}
        assert len(data) == 2

        # Each row carries a resolved display label: the workflow's name, and the
        # sync target's friendly name (not the raw "lastfm:plays" id).
        labels = {row["target_type"]: row["target_label"] for row in data}
        assert labels["workflow"] == "Test Workflow"
        assert labels["sync"] == "Last.fm plays"


class _StubTokenStorage:
    """Token storage that serves a fixed per-service map and nothing else."""

    def __init__(self, tokens: dict[str, StoredToken]) -> None:
        self._tokens = tokens

    async def load_token(self, service: str, _user_id: str) -> StoredToken | None:
        return self._tokens.get(service)

    async def load_tokens(
        self, services: Collection[str], _user_id: str
    ) -> Mapping[str, StoredToken | None]:
        return {s: self._tokens.get(s) for s in services}


type _StubTokens = Callable[[dict[str, StoredToken]], None]


@pytest.fixture
def stub_tokens(monkeypatch: pytest.MonkeyPatch) -> _StubTokens:
    """Pin which connectors this user has connected, without touching storage."""

    def _apply(tokens: dict[str, StoredToken]) -> None:
        monkeypatch.setattr(
            deps_module, "get_token_storage", lambda: _StubTokenStorage(tokens)
        )

    return _apply


class TestSyncTargetsList:
    """``GET /sync/targets`` is server truth for the Sync page's cards.

    The point of the endpoint is that adding a connector lights up the UI with
    no frontend edit, so every assertion reads the registry rather than
    restating it — a hardcoded expectation here would be the same mirror the
    endpoint exists to delete.
    """

    async def test_returns_every_dispatchable_target(
        self, client: httpx2.AsyncClient
    ) -> None:
        resp = await client.get("/api/v1/sync/targets")

        assert resp.status_code == 200
        rows = resp.json()["data"]
        assert {r["id"] for r in rows} == set(SYNC_TARGETS)

    async def test_labels_come_from_the_registry(
        self, client: httpx2.AsyncClient
    ) -> None:
        rows = (await client.get("/api/v1/sync/targets")).json()["data"]

        assert {r["id"]: r["label"] for r in rows} == {
            target: spec.label for target, spec in SYNC_TARGETS.items()
        }

    async def test_self_managed_marks_only_the_adaptive_poller(
        self, client: httpx2.AsyncClient
    ) -> None:
        # spotify:plays rewrites its own cadence, so the web must render it with
        # a toggle rather than the daily/weekly picker that would switch the
        # adaptivity off.
        rows = (await client.get("/api/v1/sync/targets")).json()["data"]

        assert {r["id"] for r in rows if r["self_managed"]} == {
            target for target, spec in SYNC_TARGETS.items() if not spec.user_schedulable
        }

    async def test_service_names_the_connector_registry_key(
        self, client: httpx2.AsyncClient
    ) -> None:
        # Not the target id's prefix: apple:plays runs on the "apple_music"
        # connector, so a frontend splitting the id would prompt the wrong card.
        rows = (await client.get("/api/v1/sync/targets")).json()["data"]

        assert {r["id"]: r["service"] for r in rows} == {
            target: spec.service for target, spec in SYNC_TARGETS.items()
        }

    async def test_connected_and_scoped_targets_are_available(
        self, client: httpx2.AsyncClient, stub_tokens: _StubTokens
    ) -> None:
        stub_tokens({
            "lastfm": StoredToken(session_key="sk"),
            "apple_music": StoredToken(access_token="mut"),
            "spotify": StoredToken(access_token="at", scope=RECENTLY_PLAYED_SCOPE),
        })

        rows = (await client.get("/api/v1/sync/targets")).json()["data"]

        assert all(r["available"] for r in rows)
        assert {r["blocked_reason"] for r in rows} == {None}

    async def test_target_without_a_token_reports_not_connected(
        self, client: httpx2.AsyncClient, stub_tokens: _StubTokens
    ) -> None:
        stub_tokens({"lastfm": StoredToken(session_key="sk")})

        rows = (await client.get("/api/v1/sync/targets")).json()["data"]
        by_id = {r["id"]: r for r in rows}

        assert by_id["lastfm:plays"]["available"] is True
        assert by_id["apple:plays"] == {
            **by_id["apple:plays"],
            "available": False,
            "blocked_reason": "CONNECTOR_NOT_CONNECTED",
        }
        assert by_id["spotify:likes"]["blocked_reason"] == "CONNECTOR_NOT_CONNECTED"

    async def test_spotify_grant_without_the_play_scope_blocks_only_plays(
        self, client: httpx2.AsyncClient, stub_tokens: _StubTokens
    ) -> None:
        # A grant minted before v0.10.1 still serves likes, so the two spotify
        # targets must disagree — exactly as their trigger routes do.
        stub_tokens({
            "spotify": StoredToken(access_token="at", scope="user-library-read")
        })

        rows = (await client.get("/api/v1/sync/targets")).json()["data"]
        by_id = {r["id"]: r for r in rows}

        assert by_id["spotify:likes"]["available"] is True
        assert by_id["spotify:plays"]["available"] is False
        assert by_id["spotify:plays"]["blocked_reason"] == "CONNECTOR_SCOPE_MISSING"

    async def test_availability_matches_the_trigger_route_409(
        self, client: httpx2.AsyncClient, stub_tokens: _StubTokens
    ) -> None:
        # The point of the flag: the list and the 409 read one predicate, so a
        # card the web enables can never be refused by its own trigger.
        stub_tokens({
            "spotify": StoredToken(access_token="at", scope="user-library-read")
        })

        rows = (await client.get("/api/v1/sync/targets")).json()["data"]
        blocked = {r["id"]: r["blocked_reason"] for r in rows}

        refused = await client.post("/api/v1/imports/spotify/recent", json={})
        assert refused.status_code == 409
        assert refused.json()["error"]["code"] == blocked["spotify:plays"]
