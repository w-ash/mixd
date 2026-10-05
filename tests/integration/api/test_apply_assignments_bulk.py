"""Integration tests for the bulk apply-assignments SSE endpoint (v0.7.7).

``POST /api/v1/playlist-assignments/apply-bulk`` returns an ``operation_id``
and writes an ``OperationRun`` row at kickoff. The background task is stubbed
by the client fixture, so the row stays ``running``; the seam's finalize path
is pinned in ``tests/unit/interface/api/services/test_sse_operations.py``.
"""

import httpx2


class TestBulkApplyAssignmentsRoute:
    async def test_kickoff_returns_202_and_writes_a_running_run_row(
        self, client: httpx2.AsyncClient
    ) -> None:
        response = await client.post("/api/v1/playlist-assignments/apply-bulk")

        assert response.status_code == 202
        body = response.json()
        assert body["operation_id"]

        run = await client.get(f"/api/v1/operation-runs/{body['run_id']}")
        assert run.status_code == 200
        row = run.json()
        assert row["operation_type"] == "apply_assignments_bulk"
        assert row["operation_id"] == body["operation_id"]
        assert row["status"] == "running"
        assert row["initiated_by"] == "manual"
