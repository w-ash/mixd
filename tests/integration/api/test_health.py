"""Tests for the health check endpoint.

Verifies the basic health probe returns expected status and version, that the
default probe never touches the database (Neon scale-to-zero — see the module
docstring on the route), that ``?deep=true`` does, that ``?busy=true`` reports
in-flight work for the pre-deploy gate, and that the error middleware handles
unexpected errors correctly.
"""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx2

from src import __version__
from src.application.services.operation_run_reaper import RunningRunCounts


class TestHealthEndpoint:
    """GET /api/v1/health returns service status."""

    async def test_health_returns_ok(self, client: httpx2.AsyncClient) -> None:
        response = await client.get("/api/v1/health")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["version"] == __version__
        # chat_available was removed (X2): the real chat gate is per-user
        # (/assistant/status), so the honest server-only field replaces it.
        assert "chat_available" not in body
        assert isinstance(body["server_anthropic_key_configured"], bool)

    async def test_health_content_type_is_json(
        self, client: httpx2.AsyncClient
    ) -> None:
        response = await client.get("/api/v1/health")

        assert response.headers["content-type"] == "application/json"


class TestHealthDoesNotWakeTheDatabase:
    """The default probe must issue zero queries.

    Fly and Docker both hit this every 30s; a query here would reset Neon's
    5-minute scale-to-zero timer forever and bill the compute 24/7.
    """

    async def test_shallow_health_never_touches_the_engine(
        self, client: httpx2.AsyncClient
    ) -> None:
        with patch("src.interface.api.routes.health.get_engine") as mock_get_engine:
            response = await client.get("/api/v1/health")

        assert response.status_code == 200
        mock_get_engine.assert_not_called()
        assert "database" not in response.json()

    async def test_deep_health_probes_the_database(
        self, client: httpx2.AsyncClient
    ) -> None:
        response = await client.get("/api/v1/health?deep=true")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["database"] == "connected"

    async def test_deep_health_reports_503_when_database_is_unreachable(
        self, client: httpx2.AsyncClient
    ) -> None:
        with patch(
            "src.interface.api.routes.health._probe_database",
            return_value="Database unavailable",
        ):
            response = await client.get("/api/v1/health?deep=true")

        assert response.status_code == 503
        body = response.json()
        assert body["status"] == "degraded"
        assert body["database"] == "unavailable"
        assert body["database_error"] == "Database unavailable"


class TestBusyProbe:
    """``?busy=true`` reports in-flight work for release.yml's deploy gate.

    Both halves matter: started runs live in ``operation_runs``, but a
    queued-not-started export file has no row — only the in-process queue
    registry knows about it.
    """

    async def test_idle_system_reports_not_busy(
        self, client: httpx2.AsyncClient
    ) -> None:
        response = await client.get("/api/v1/health?busy=true")

        assert response.status_code == 200
        body = response.json()
        assert body["busy"] is False
        assert body["running_operation_runs"] == 0
        assert body["stale_running_operation_runs"] == 0
        assert body["import_queue_pending"] is False
        assert body["uploads_streaming"] == 0

    async def test_shallow_health_omits_busy_fields(
        self, client: httpx2.AsyncClient
    ) -> None:
        body = (await client.get("/api/v1/health")).json()

        assert "busy" not in body
        assert "running_operation_runs" not in body

    async def test_running_operation_run_marks_busy(
        self, client: httpx2.AsyncClient
    ) -> None:
        # ``_probe_busy`` imports the counter at call time, so patching the
        # source module intercepts the DB half without needing a committed row
        # visible to the app's own engine.
        with patch(
            "src.application.services.operation_run_reaper.count_running_runs",
            new=AsyncMock(return_value=RunningRunCounts(live=1, stale=0)),
        ):
            response = await client.get("/api/v1/health?busy=true")

        body = response.json()
        assert body["busy"] is True
        assert body["running_operation_runs"] == 1
        assert body["import_queue_pending"] is False

    async def test_stale_rows_are_reported_but_do_not_block(
        self, client: httpx2.AsyncClient
    ) -> None:
        # A running row older than REAP_AGE_BOUND is reaper-dead: excluded
        # from ``busy`` (anti-deadlock — the deploy is the restart that reaps
        # it) but surfaced so release.yml can warn instead of hiding it.
        with patch(
            "src.application.services.operation_run_reaper.count_running_runs",
            new=AsyncMock(return_value=RunningRunCounts(live=0, stale=2)),
        ):
            response = await client.get("/api/v1/health?busy=true")

        body = response.json()
        assert body["busy"] is False
        assert body["running_operation_runs"] == 0
        assert body["stale_running_operation_runs"] == 2

    async def test_streaming_upload_marks_busy(
        self, client: httpx2.AsyncClient
    ) -> None:
        # The third signal: while a POST is streaming files to disk there is
        # no queue registered and no run row — only the streaming counter can
        # stop a deploy landing mid-upload.
        from src.interface.api.services.import_queue import streaming_upload

        with streaming_upload():
            response = await client.get("/api/v1/health?busy=true")

        body = response.json()
        assert body["busy"] is True
        assert body["uploads_streaming"] == 1
        assert body["running_operation_runs"] == 0
        assert body["import_queue_pending"] is False

    async def test_db_error_reports_busy_with_error_marker(
        self, client: httpx2.AsyncClient
    ) -> None:
        # Fail-closed: a SERVING app whose count query errors proves nothing
        # about in-flight work. A 500 here reads as "unreachable" to
        # release.yml's ``curl -fsS ... || true`` and would deploy over a live
        # import — so the probe answers 200 busy=true and lets the gate retry.
        with patch(
            "src.application.services.operation_run_reaper.count_running_runs",
            new=AsyncMock(side_effect=TimeoutError("connection pool exhausted")),
        ):
            response = await client.get("/api/v1/health?busy=true")

        assert response.status_code == 200
        body = response.json()
        assert body["busy"] is True
        assert body["busy_probe_error"] == "TimeoutError"
        assert body["import_queue_pending"] is False
        # The count is unknown, not zero — reporting 0 would contradict busy.
        assert "running_operation_runs" not in body

    async def test_pending_queue_entry_marks_busy(
        self, client: httpx2.AsyncClient
    ) -> None:
        from src.interface.api.services import import_queue
        from src.interface.api.services.import_queue import ImportQueue, QueueEntry

        import_queue._queues["u1"] = ImportQueue(
            queue_id="q1",
            user_id="u1",
            tmpdir=Path("/nonexistent"),
            entries=[
                QueueEntry(
                    filename="f.json",
                    position=0,
                    path=Path("/nonexistent/f.json"),
                )
            ],
        )
        try:
            response = await client.get("/api/v1/health?busy=true")
        finally:
            _ = import_queue._queues.pop("u1", None)

        body = response.json()
        assert body["busy"] is True
        assert body["running_operation_runs"] == 0
        assert body["import_queue_pending"] is True


class TestErrorHandling:
    """Global exception handlers produce standard error envelopes."""

    async def test_not_found_route_returns_404(
        self, client: httpx2.AsyncClient
    ) -> None:
        response = await client.get("/api/v1/nonexistent")

        assert response.status_code == 404

    async def test_openapi_schema_accessible(self, client: httpx2.AsyncClient) -> None:
        response = await client.get("/api/openapi.json")

        assert response.status_code == 200
        schema = response.json()
        assert schema["info"]["title"] == "Mixd"
        assert "paths" in schema
