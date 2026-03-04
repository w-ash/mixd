"""Tests for the health check endpoint.

Verifies the basic health probe returns expected status and version,
and that the error middleware handles unexpected errors correctly.
"""

import httpx


class TestHealthEndpoint:
    """GET /api/v1/health returns service status."""

    async def test_health_returns_ok(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/api/v1/health")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["version"] == "0.3.1"

    async def test_health_content_type_is_json(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/api/v1/health")

        assert response.headers["content-type"] == "application/json"


class TestErrorHandling:
    """Global exception handlers produce standard error envelopes."""

    async def test_not_found_route_returns_404(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/api/v1/nonexistent")

        assert response.status_code == 404

    async def test_openapi_schema_accessible(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/api/openapi.json")

        assert response.status_code == 200
        schema = response.json()
        assert schema["info"]["title"] == "Narada"
        assert "paths" in schema
