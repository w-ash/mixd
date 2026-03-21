"""Tests that security headers are present on all HTTP responses."""

import httpx


class TestSecurityHeaders:
    """Security headers must appear on every response regardless of status code."""

    async def test_success_response_has_security_headers(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/api/v1/health")
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-frame-options"] == "DENY"
        assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"

    async def test_not_found_response_has_security_headers(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/api/v1/nonexistent")
        assert response.status_code == 404
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-frame-options"] == "DENY"
        assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"

    async def test_validation_error_response_has_security_headers(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.post(
            "/api/v1/imports/lastfm/history", json={"mode": "invalid"}
        )
        assert response.status_code == 422
        assert response.headers["x-content-type-options"] == "nosniff"
