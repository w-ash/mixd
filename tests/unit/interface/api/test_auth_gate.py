"""Tests for NeonAuthMiddleware.

Exercises every code path in the ASGI auth gate: non-API paths pass through,
API paths require Bearer tokens, exempt API paths (health), JWT validation
(valid, expired, bad signature, JWKS failure), and email allowlist.

Uses a pure ASGI test harness — no FastAPI, no HTTP server.
"""

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import jwt

import src.interface.api.auth_gate as auth_gate_mod
from src.interface.api.auth_gate import NeonAuthMiddleware
from tests.fixtures.auth_keys import TEST_JWK_SET, sign_test_jwt

# ---------------------------------------------------------------------------
# Test harness
# ---------------------------------------------------------------------------


class _FakeApp:
    """Records whether the inner ASGI app was called and captures scope."""

    def __init__(self) -> None:
        self.called = False
        self.captured_scope: dict = {}

    async def __call__(self, scope: dict, receive: object, send: object) -> None:
        self.called = True
        self.captured_scope = dict(scope)


def _make_scope(
    path: str = "/api/v1/tracks",
    *,
    scope_type: str = "http",
    headers: list[tuple[bytes, bytes]] | None = None,
) -> dict[str, Any]:
    """Build a minimal ASGI HTTP scope dict."""
    return {
        "type": scope_type,
        "path": path,
        "headers": headers or [],
    }


def _bearer_header(token: str) -> tuple[bytes, bytes]:
    return (b"authorization", f"Bearer {token}".encode())


class _ResponseCapture:
    """Collects ASGI send messages for assertion."""

    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def __call__(self, message: dict) -> None:
        self.messages.append(message)

    @property
    def status(self) -> int:
        return self.messages[0]["status"]

    @property
    def body(self) -> bytes:
        return self.messages[1].get("body", b"")

    @property
    def body_json(self) -> dict:
        return json.loads(self.body)

    def header(self, name: bytes) -> bytes | None:
        for key, value in self.messages[0].get("headers", []):
            if key == name:
                return value
        return None


async def _noop_receive() -> dict:
    return {"type": "http.request", "body": b""}


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------


class TestNonApiPaths:
    """Non-API paths pass through without auth (SPA shell, assets)."""

    async def test_root_passes_through(self):
        mw, inner = _make_middleware()
        scope = _make_scope(path="/")
        send = _ResponseCapture()

        await mw(scope, _noop_receive, send)

        assert inner.called is True

    async def test_auth_page_passes_through(self):
        mw, inner = _make_middleware()
        scope = _make_scope(path="/auth/sign-in")
        send = _ResponseCapture()

        await mw(scope, _noop_receive, send)

        assert inner.called is True

    async def test_assets_pass_through(self):
        mw, inner = _make_middleware()
        scope = _make_scope(path="/assets/index-abc123.js")
        send = _ResponseCapture()

        await mw(scope, _noop_receive, send)

        assert inner.called is True

    async def test_favicon_passes_through(self):
        mw, inner = _make_middleware()
        scope = _make_scope(path="/favicon.svg")
        send = _ResponseCapture()

        await mw(scope, _noop_receive, send)

        assert inner.called is True


# ---------------------------------------------------------------------------
# Middleware tests — shared setup
# ---------------------------------------------------------------------------


def _make_middleware(
    app: _FakeApp | None = None,
    *,
    allowed_emails: frozenset[str] | None = None,
) -> tuple[NeonAuthMiddleware, _FakeApp]:
    inner = app or _FakeApp()
    mw = NeonAuthMiddleware(
        inner,
        jwks_url="https://test.neonauth.example/.well-known/jwks.json",
        allowed_emails=allowed_emails,
    )
    return mw, inner


class TestNonHttpScope:
    """Middleware passes through non-HTTP scopes untouched."""

    async def test_websocket_scope_passes_through(self):
        mw, inner = _make_middleware()
        scope = _make_scope(scope_type="websocket")
        send = _ResponseCapture()

        await mw(scope, _noop_receive, send)

        assert inner.called is True

    async def test_lifespan_scope_passes_through(self):
        mw, inner = _make_middleware()
        scope = _make_scope(scope_type="lifespan")
        send = _ResponseCapture()

        await mw(scope, _noop_receive, send)

        assert inner.called is True


class TestExemptApiPaths:
    """Exempt API paths bypass auth (health check)."""

    async def test_health_endpoint_exempt(self):
        mw, inner = _make_middleware()
        scope = _make_scope(path="/api/v1/health")
        send = _ResponseCapture()

        await mw(scope, _noop_receive, send)

        assert inner.called is True


class TestBearerTokenAuth:
    """Bearer token validation: valid JWT, expired JWT, bad sig, allowlist."""

    def setup_method(self):
        auth_gate_mod._jwks_cache = (None, 0.0)

    @patch.object(auth_gate_mod, "get_jwk_set", new_callable=AsyncMock)
    async def test_valid_jwt_attaches_claims_to_scope(self, mock_jwks: AsyncMock):
        mock_jwks.return_value = TEST_JWK_SET
        token = sign_test_jwt(sub="user-42", email="me@mixd.app")
        mw, inner = _make_middleware()
        scope = _make_scope(headers=[_bearer_header(token)])
        send = _ResponseCapture()

        await mw(scope, _noop_receive, send)

        assert inner.called is True
        assert inner.captured_scope["auth_user"]["sub"] == "user-42"

    @patch.object(auth_gate_mod, "get_jwk_set", new_callable=AsyncMock)
    async def test_expired_jwt_returns_401(self, mock_jwks: AsyncMock):
        mock_jwks.return_value = TEST_JWK_SET
        token = sign_test_jwt(exp_delta=-3600)  # expired 1 hour ago
        mw, inner = _make_middleware()
        scope = _make_scope(headers=[_bearer_header(token)])
        send = _ResponseCapture()

        await mw(scope, _noop_receive, send)

        assert inner.called is False
        assert send.status == 401

    @patch.object(auth_gate_mod, "get_jwk_set", new_callable=AsyncMock)
    async def test_invalid_signature_returns_401(self, mock_jwks: AsyncMock):
        mock_jwks.return_value = TEST_JWK_SET
        # Sign with a different key → signature mismatch
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        wrong_key = Ed25519PrivateKey.generate()
        token = jwt.encode(
            {"sub": "hacker", "exp": 9999999999},
            wrong_key,
            algorithm="EdDSA",
        )
        mw, inner = _make_middleware()
        scope = _make_scope(headers=[_bearer_header(token)])
        send = _ResponseCapture()

        await mw(scope, _noop_receive, send)

        assert inner.called is False
        assert send.status == 401

    @patch.object(auth_gate_mod, "get_jwk_set", new_callable=AsyncMock)
    async def test_jwks_fetch_failure_returns_401(self, mock_jwks: AsyncMock):
        mock_jwks.side_effect = httpx.ConnectError("connection refused")
        token = sign_test_jwt()
        mw, inner = _make_middleware()
        scope = _make_scope(headers=[_bearer_header(token)])
        send = _ResponseCapture()

        await mw(scope, _noop_receive, send)

        assert inner.called is False
        assert send.status == 401

    @patch.object(auth_gate_mod, "get_jwk_set", new_callable=AsyncMock)
    async def test_email_in_allowlist_passes(self, mock_jwks: AsyncMock):
        mock_jwks.return_value = TEST_JWK_SET
        token = sign_test_jwt(email="me@mixd.app")
        mw, inner = _make_middleware(allowed_emails=frozenset({"me@mixd.app"}))
        scope = _make_scope(headers=[_bearer_header(token)])
        send = _ResponseCapture()

        await mw(scope, _noop_receive, send)

        assert inner.called is True

    @patch.object(auth_gate_mod, "get_jwk_set", new_callable=AsyncMock)
    async def test_email_not_in_allowlist_returns_403(self, mock_jwks: AsyncMock):
        mock_jwks.return_value = TEST_JWK_SET
        token = sign_test_jwt(email="outsider@evil.com")
        mw, inner = _make_middleware(allowed_emails=frozenset({"me@mixd.app"}))
        scope = _make_scope(headers=[_bearer_header(token)])
        send = _ResponseCapture()

        await mw(scope, _noop_receive, send)

        assert inner.called is False
        assert send.status == 403
        assert send.body_json["error"]["code"] == "FORBIDDEN"

    @patch.object(auth_gate_mod, "get_jwk_set", new_callable=AsyncMock)
    async def test_no_allowlist_anyone_passes(self, mock_jwks: AsyncMock):
        mock_jwks.return_value = TEST_JWK_SET
        token = sign_test_jwt(email="anyone@anywhere.com")
        mw, inner = _make_middleware(allowed_emails=None)
        scope = _make_scope(headers=[_bearer_header(token)])
        send = _ResponseCapture()

        await mw(scope, _noop_receive, send)

        assert inner.called is True


class TestNoCredentials:
    """API requests without Bearer token get 401."""

    async def test_no_bearer_returns_401(self):
        mw, inner = _make_middleware()
        scope = _make_scope()
        send = _ResponseCapture()

        await mw(scope, _noop_receive, send)

        assert inner.called is False
        assert send.status == 401
        assert send.body_json["error"]["code"] == "UNAUTHORIZED"
        assert send.header(b"www-authenticate") == b'Bearer realm="mixd"'
