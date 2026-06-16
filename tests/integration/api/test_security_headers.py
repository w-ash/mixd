"""Tests that security headers are present on all HTTP responses."""

from unittest.mock import AsyncMock, patch

import httpx

from src.infrastructure.connectors._shared.token_storage import StoredToken


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
        # Report Last.fm connected so the 6c pre-flight passes and request reaches
        # body validation (the point of this test — security headers on a 422).
        storage = AsyncMock()
        storage.load_token = AsyncMock(
            return_value=StoredToken(account_name="x", session_key="sk")
        )
        with patch("src.interface.api.deps.get_token_storage", return_value=storage):
            response = await client.post(
                "/api/v1/imports/lastfm/history", json={"mode": "invalid"}
            )
        assert response.status_code == 422
        assert response.headers["x-content-type-options"] == "nosniff"
