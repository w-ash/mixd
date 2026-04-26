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
) -> OperationRun:
    """Persist one OperationRun row and return the saved domain entity."""
    run = make_operation_run(
        user_id=user_id,
        operation_type=operation_type,
        started_at=started_at or datetime.now(UTC),
        status=status,
        counts=counts,
        issues=issues,
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
