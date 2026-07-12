"""Integration tests for the OperationRun audit-log endpoints (v0.7.7).

Covers GET /api/v1/operation-runs (paginated list) and
GET /api/v1/operation-runs/{id} (full detail). The tests seed rows
directly via ``execute_use_case`` and read them back through the API
to exercise the cursor pagination, type filtering, and the
deliberate-404-for-non-owner contract.
"""

from datetime import UTC, datetime, timedelta

import httpx

from src.application.runner import execute_use_case
from src.domain.entities.operation_run import OperationRun
from tests.fixtures import make_operation_run


async def _seed_run(
    user_id: str = "default",
    operation_type: str = "import_lastfm_history",
    started_at: datetime | None = None,
    status: str = "complete",
    counts: dict | None = None,
    issues: list | None = None,
    operation_id: str | None = None,
    request_params: dict | None = None,
    initiated_by: str = "manual",
) -> OperationRun:
    """Persist one OperationRun row and return the saved domain entity."""
    run = make_operation_run(
        user_id=user_id,
        operation_type=operation_type,
        started_at=started_at or datetime.now(UTC),
        status=status,
        counts=counts,
        issues=issues,
        operation_id=operation_id,
        request_params=request_params if request_params is not None else {},
        initiated_by=initiated_by,
    )

    async def _do(uow):
        async with uow:
            saved = await uow.get_operation_run_repository().create(run)
            await uow.commit()
            return saved

    return await execute_use_case(_do, user_id=user_id)


class TestListOperationRuns:
    """GET /api/v1/operation-runs — pagination, filtering, scoping."""

    async def test_returns_empty_data_when_none(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/api/v1/operation-runs")
        assert response.status_code == 200
        body = response.json()
        assert body["data"] == []
        assert body["next_cursor"] is None
        assert body["limit"] == 20

    async def test_returns_user_runs_newest_first(
        self, client: httpx.AsyncClient
    ) -> None:
        now = datetime.now(UTC)
        await _seed_run(started_at=now - timedelta(hours=2))
        middle = await _seed_run(started_at=now - timedelta(hours=1))
        await _seed_run(started_at=now)

        response = await client.get("/api/v1/operation-runs")
        assert response.status_code == 200
        rows = response.json()["data"]
        assert len(rows) == 3
        assert rows[1]["id"] == str(middle.id)

    async def test_default_filter_excludes_non_import_types(
        self, client: httpx.AsyncClient
    ) -> None:
        """The default ``type=imports`` filter hides workflow_run / playlist_sync."""
        await _seed_run(operation_type="import_lastfm_history")
        await _seed_run(operation_type="workflow_run")
        await _seed_run(operation_type="playlist_sync")

        response = await client.get("/api/v1/operation-runs")
        assert response.status_code == 200
        types = {r["operation_type"] for r in response.json()["data"]}
        assert "import_lastfm_history" in types
        assert "workflow_run" not in types
        assert "playlist_sync" not in types

    async def test_type_all_returns_everything(self, client: httpx.AsyncClient) -> None:
        await _seed_run(operation_type="import_lastfm_history")
        await _seed_run(operation_type="workflow_run")

        response = await client.get("/api/v1/operation-runs?type=all")
        assert response.status_code == 200
        types = {r["operation_type"] for r in response.json()["data"]}
        assert types == {"import_lastfm_history", "workflow_run"}

    async def test_pagination_returns_next_cursor(
        self, client: httpx.AsyncClient
    ) -> None:
        now = datetime.now(UTC)
        for i in range(5):
            await _seed_run(started_at=now - timedelta(minutes=i))

        page1 = (await client.get("/api/v1/operation-runs?limit=2")).json()
        assert len(page1["data"]) == 2
        assert page1["next_cursor"] is not None

        page2 = (
            await client.get(
                f"/api/v1/operation-runs?limit=2&cursor={page1['next_cursor']}"
            )
        ).json()
        assert len(page2["data"]) == 2
        assert page2["next_cursor"] is not None

        # No overlap, no skip.
        page1_ids = {r["id"] for r in page1["data"]}
        page2_ids = {r["id"] for r in page2["data"]}
        assert page1_ids.isdisjoint(page2_ids)

    async def test_summary_omits_full_issues_payload(
        self, client: httpx.AsyncClient
    ) -> None:
        await _seed_run(
            issues=[{"track_id": "abc"}, {"track_id": "def"}],
        )

        response = await client.get("/api/v1/operation-runs")
        assert response.status_code == 200
        row = response.json()["data"][0]
        assert "issues" not in row
        assert row["issue_count"] == 2

    async def test_summary_exposes_initiated_by(
        self, client: httpx.AsyncClient
    ) -> None:
        """The list row carries the attribution so the UI can badge agent runs."""
        await _seed_run(initiated_by="assistant")

        response = await client.get("/api/v1/operation-runs")
        assert response.status_code == 200
        assert response.json()["data"][0]["initiated_by"] == "assistant"


class TestActiveOperationAwareness:
    """GET /api/v1/operation-runs?status=running — the operation-awareness query.

    Reuses the audit list (DRY): the frontend re-attaches to in-flight runs by
    filtering on status and streaming each row's ``operation_id``.
    """

    async def test_status_running_filters_to_in_flight(
        self, client: httpx.AsyncClient
    ) -> None:
        await _seed_run(status="running", operation_id="op-live")
        await _seed_run(status="complete")
        await _seed_run(status="error")

        response = await client.get("/api/v1/operation-runs?status=running&type=all")
        assert response.status_code == 200
        rows = response.json()["data"]
        assert len(rows) == 1
        assert rows[0]["status"] == "running"

    async def test_rows_expose_operation_id_for_reattach(
        self, client: httpx.AsyncClient
    ) -> None:
        await _seed_run(status="running", operation_id="op-reattach")

        rows = (
            await client.get("/api/v1/operation-runs?status=running&type=all")
        ).json()["data"]
        # The SSE handle the frontend needs to re-open GET /operations/{id}/progress.
        assert rows[0]["operation_id"] == "op-reattach"

    async def test_detail_also_exposes_operation_id(
        self, client: httpx.AsyncClient
    ) -> None:
        run = await _seed_run(operation_id="op-detail")

        body = (await client.get(f"/api/v1/operation-runs/{run.id}")).json()
        assert body["operation_id"] == "op-detail"


class TestGetOperationRun:
    """GET /api/v1/operation-runs/{run_id} — full detail with 404 for non-owner."""

    async def test_owner_gets_full_payload(self, client: httpx.AsyncClient) -> None:
        run = await _seed_run(
            counts={"tracks": 100},
            issues=[{"track_id": "abc", "reason": "no_match"}],
        )

        response = await client.get(f"/api/v1/operation-runs/{run.id}")
        assert response.status_code == 200
        body = response.json()
        assert body["id"] == str(run.id)
        assert body["counts"] == {"tracks": 100}
        assert body["issues"] == [{"track_id": "abc", "reason": "no_match"}]
        # Manual is the default attribution for a directly-initiated run.
        assert body["initiated_by"] == "manual"

    async def test_detail_exposes_assistant_attribution(
        self, client: httpx.AsyncClient
    ) -> None:
        run = await _seed_run(initiated_by="assistant")

        body = (await client.get(f"/api/v1/operation-runs/{run.id}")).json()
        assert body["initiated_by"] == "assistant"

    async def test_non_existent_returns_404(self, client: httpx.AsyncClient) -> None:
        from uuid import uuid7

        response = await client.get(f"/api/v1/operation-runs/{uuid7()}")
        assert response.status_code == 404

    async def test_non_owner_returns_404_not_403(
        self, client: httpx.AsyncClient
    ) -> None:
        """Existence-leak guard: another user's run looks identical to a missing one."""
        run = await _seed_run(user_id="alice")

        # The test client authenticates as "default" (no JWT in scope), so
        # alice's run is not visible to it.
        response = await client.get(f"/api/v1/operation-runs/{run.id}")
        assert response.status_code == 404


class TestRetryFailed:
    """POST /api/v1/operation-runs/{run_id}/retry-failed."""

    @staticmethod
    def _failed_import_run(**overrides):
        """An import run with two failed playlists, ready to retry."""
        overrides.setdefault("status", "error")
        return _seed_run(
            operation_type="import_connector_playlists",
            issues=[
                {"connector_playlist_identifier": "plA", "message": "boom"},
                {"connector_playlist_identifier": "plC", "message": "boom"},
            ],
            request_params={"connector_name": "spotify", "sync_direction": "pull"},
            **overrides,
        )

    async def test_missing_run_returns_404(self, client: httpx.AsyncClient) -> None:
        from uuid import uuid7

        response = await client.post(f"/api/v1/operation-runs/{uuid7()}/retry-failed")
        assert response.status_code == 404

    async def test_still_running_returns_409(self, client: httpx.AsyncClient) -> None:
        run = await self._failed_import_run(status="running")
        response = await client.post(f"/api/v1/operation-runs/{run.id}/retry-failed")
        assert response.status_code == 409

    async def test_no_failed_items_returns_409(self, client: httpx.AsyncClient) -> None:
        run = await _seed_run(
            operation_type="import_connector_playlists",
            status="error",
            issues=[],
            request_params={"connector_name": "spotify", "sync_direction": "pull"},
        )
        response = await client.post(f"/api/v1/operation-runs/{run.id}/retry-failed")
        assert response.status_code == 409

    async def test_non_import_type_returns_409(self, client: httpx.AsyncClient) -> None:
        run = await _seed_run(
            operation_type="import_lastfm_history",
            status="error",
            issues=[{"connector_playlist_identifier": "plA", "message": "boom"}],
            request_params={"connector_name": "spotify", "sync_direction": "pull"},
        )
        response = await client.post(f"/api/v1/operation-runs/{run.id}/retry-failed")
        assert response.status_code == 409

    async def test_happy_path_returns_202(self, client: httpx.AsyncClient) -> None:
        """A failed import run with stored params + failed ids is retryable.

        The conftest no-ops the background spawner, so this exercises the
        validation + 202 contract without running the real import.
        """
        run = await self._failed_import_run()
        response = await client.post(f"/api/v1/operation-runs/{run.id}/retry-failed")
        assert response.status_code == 202
        body = response.json()
        assert isinstance(body.get("operation_id"), str)
        assert body["operation_id"]

    async def test_reinvokes_use_case_with_failed_subset_and_auth_owner(
        self, client: httpx.AsyncClient
    ) -> None:
        """The retry rebuilds the call from the run: only failed ids, stored
        connector + direction, and the owner from auth (never stored data)."""
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, patch
        from uuid import uuid7

        from src.domain.entities.playlist_link import SyncDirection
        import src.interface.api.routes.operation_runs as ops_route
        from src.interface.api.schemas.imports import OperationStartedResponse

        run = await self._failed_import_run()
        mock_import = AsyncMock(return_value=object())

        async def fake_launch(*, coro_factory, **_kwargs) -> OperationStartedResponse:
            await coro_factory(SimpleNamespace(operation_id="op-new", run_id=uuid7()))
            return OperationStartedResponse(operation_id="op-new", run_id="run-new")

        with (
            patch.object(
                ops_route,
                "run_import_connector_playlists_as_canonical",
                new=mock_import,
            ),
            patch.object(ops_route, "to_operation_result", new=lambda r: r),
            patch.object(ops_route, "launch_sse_operation", new=fake_launch),
        ):
            response = await client.post(
                f"/api/v1/operation-runs/{run.id}/retry-failed"
            )

        assert response.status_code == 202
        mock_import.assert_awaited_once()
        kwargs = mock_import.await_args.kwargs
        assert kwargs["user_id"] == "default"
        assert kwargs["connector_name"] == "spotify"
        assert kwargs["sync_direction"] == SyncDirection.PULL
        assert [str(x) for x in kwargs["connector_playlist_identifiers"]] == [
            "plA",
            "plC",
        ]
