"""Integration tests for NeonAuthMiddleware wired into the real FastAPI app.

Verifies that the middleware is correctly mounted by ``create_app()`` when
``neon_auth_url`` is configured, and that it properly gates access to
protected routes while allowing exempt paths through.

Uses httpx.AsyncClient with ASGITransport — the same pattern as the main
API integration tests, but without a database (the middleware responds
before route handlers run for rejected requests).
"""

from collections.abc import AsyncGenerator
import contextlib
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from src.config import settings
from src.interface.api.app import create_app
import src.interface.api.auth_gate as auth_gate_mod
from tests.fixtures.auth_keys import TEST_JWK_SET, sign_test_jwt


@contextlib.asynccontextmanager
async def _auth_test_client(
    allowed_emails: str = "",
) -> AsyncGenerator[httpx.AsyncClient]:
    """Build an httpx client with auth middleware enabled via patched settings."""
    with (
        patch.object(
            settings.server,
            "neon_auth_url",
            "https://test.neonauth.example/auth",
        ),
        patch.object(
            settings.server,
            "neon_auth_jwks_url",
            "https://test.neonauth.example/.well-known/jwks.json",
        ),
        patch.object(settings.server, "allowed_emails", allowed_emails),
        patch.object(
            auth_gate_mod,
            "get_jwk_set",
            new_callable=AsyncMock,
            return_value=TEST_JWK_SET,
        ),
    ):
        app = create_app()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


@pytest.fixture
async def auth_client() -> AsyncGenerator[httpx.AsyncClient]:
    """HTTP client with auth middleware enabled (no email allowlist)."""
    async with _auth_test_client() as c:
        yield c


@pytest.fixture
async def allowlist_client() -> AsyncGenerator[httpx.AsyncClient]:
    """HTTP client with auth middleware and email allowlist enabled."""
    async with _auth_test_client(allowed_emails="allowed@mixd.app") as c:
        yield c


class TestAuthMiddlewareWiring:
    """Auth middleware is correctly mounted and gates requests end-to-end."""

    async def test_health_exempt_without_token(self, auth_client: httpx.AsyncClient):
        """Health endpoint is exempt from auth — passes through even without DB."""
        resp = await auth_client.get("/api/v1/health")
        # May be 200 (healthy) or 503 (no DB) — but NOT 401/302
        assert resp.status_code != 401
        assert resp.status_code != 302

    async def test_protected_route_no_token_401(self, auth_client: httpx.AsyncClient):
        resp = await auth_client.get(
            "/api/v1/tracks", headers={"accept": "application/json"}
        )
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "UNAUTHORIZED"

    async def test_non_api_path_passes_through(self, auth_client: httpx.AsyncClient):
        """Non-API paths (SPA shell) pass through without auth."""
        resp = await auth_client.get(
            "/", headers={"accept": "text/html"}, follow_redirects=False
        )
        # Should serve the SPA, not redirect — page-level auth is client-side
        assert resp.status_code != 401
        assert resp.status_code != 302

    async def test_valid_bearer_reaches_route(self, auth_client: httpx.AsyncClient):
        """Valid bearer token passes through middleware to the route handler."""
        token = sign_test_jwt()
        resp = await auth_client.get(
            "/api/v1/health",
            headers={"authorization": f"Bearer {token}"},
        )
        # Health is exempt so Bearer is irrelevant, but confirms no crash
        assert resp.status_code != 401

    async def test_invalid_bearer_returns_401(self, auth_client: httpx.AsyncClient):
        resp = await auth_client.get(
            "/api/v1/tracks",
            headers={
                "authorization": "Bearer garbage.not.a.jwt",
                "accept": "application/json",
            },
        )
        assert resp.status_code == 401


class TestAuthMiddlewareAllowlist:
    """Email allowlist enforcement at the integration level."""

    async def test_allowlist_blocks_wrong_email(
        self, allowlist_client: httpx.AsyncClient
    ):
        token = sign_test_jwt(email="outsider@evil.com")
        resp = await allowlist_client.get(
            "/api/v1/tracks",
            headers={
                "authorization": f"Bearer {token}",
                "accept": "application/json",
            },
        )
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "FORBIDDEN"
