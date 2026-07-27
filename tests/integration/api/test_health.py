"""Tests for the health check endpoint.

Verifies the basic health probe returns expected status and version, that the
default probe never touches the database (Neon scale-to-zero — see the module
docstring on the route), that ``?deep=true`` does, and that the error middleware
handles unexpected errors correctly.
"""

from unittest.mock import patch

import httpx

from src import __version__


class TestHealthEndpoint:
    """GET /api/v1/health returns service status."""

    async def test_health_returns_ok(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/api/v1/health")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["version"] == __version__
        # chat_available was removed (X2): the real chat gate is per-user
        # (/assistant/status), so the honest server-only field replaces it.
        assert "chat_available" not in body
        assert isinstance(body["server_anthropic_key_configured"], bool)

    async def test_health_content_type_is_json(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/api/v1/health")

        assert response.headers["content-type"] == "application/json"


class TestHealthDoesNotWakeTheDatabase:
    """The default probe must issue zero queries.

    Fly and Docker both hit this every 30s; a query here would reset Neon's
    5-minute scale-to-zero timer forever and bill the compute 24/7.
    """

    async def test_shallow_health_never_touches_the_engine(
        self, client: httpx.AsyncClient
    ) -> None:
        with patch("src.interface.api.routes.health.get_engine") as mock_get_engine:
            response = await client.get("/api/v1/health")

        assert response.status_code == 200
        mock_get_engine.assert_not_called()
        assert "database" not in response.json()

    async def test_deep_health_probes_the_database(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/api/v1/health?deep=true")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["database"] == "connected"

    async def test_deep_health_reports_503_when_database_is_unreachable(
        self, client: httpx.AsyncClient
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


class TestErrorHandling:
    """Global exception handlers produce standard error envelopes."""

    async def test_not_found_route_returns_404(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/api/v1/nonexistent")

        assert response.status_code == 404

    async def test_openapi_schema_accessible(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/api/openapi.json")

        assert response.status_code == 200
        schema = response.json()
        assert schema["info"]["title"] == "Mixd"
        assert "paths" in schema
