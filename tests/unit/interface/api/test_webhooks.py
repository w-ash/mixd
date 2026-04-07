"""Tests for Neon Auth webhook endpoint.

Exercises signature verification (EdDSA detached JWS), timestamp
freshness, event dispatching (user.before_create, user.created),
and error handling (missing headers, invalid body, unknown events).
"""

import json
import time
from unittest.mock import AsyncMock, patch

import src.interface.api.routes.webhooks as webhooks_mod
from tests.fixtures.auth_keys import TEST_JWK_SET, sign_test_webhook

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _webhook_body(event_type: str, **user_fields: str) -> bytes:
    """Build a Neon Auth webhook payload."""
    user = {"id": "usr-1", "email": "test@example.com", **user_fields}
    return json.dumps({"event_type": event_type, "event_data": {"user": user}}).encode()


# ---------------------------------------------------------------------------
# Signature verification
# ---------------------------------------------------------------------------


class TestSignatureVerification:
    """EdDSA detached JWS signature verification."""

    @patch.object(webhooks_mod, "get_jwk_set", new_callable=AsyncMock)
    async def test_valid_signature_passes(self, mock_jwks: AsyncMock):
        mock_jwks.return_value = TEST_JWK_SET
        body = b'{"event_type":"user.created","event_data":{"user":{"id":"1"}}}'
        sig, kid, ts = sign_test_webhook(body)

        result = await webhooks_mod._verify_signature(
            body, sig, kid, ts, "https://test.example/.well-known/jwks.json"
        )

        assert result is True

    @patch.object(webhooks_mod, "get_jwk_set", new_callable=AsyncMock)
    async def test_tampered_body_fails(self, mock_jwks: AsyncMock):
        mock_jwks.return_value = TEST_JWK_SET
        body = b'{"event_type":"user.created","event_data":{"user":{"id":"1"}}}'
        sig, kid, ts = sign_test_webhook(body)

        # Tamper with the body after signing
        result = await webhooks_mod._verify_signature(
            b'{"event_type":"user.created","event_data":{"user":{"id":"HACKED"}}}',
            sig,
            kid,
            ts,
            "https://test.example/.well-known/jwks.json",
        )

        assert result is False

    @patch.object(webhooks_mod, "get_jwk_set", new_callable=AsyncMock)
    async def test_expired_timestamp_fails(self, mock_jwks: AsyncMock):
        mock_jwks.return_value = TEST_JWK_SET
        body = b'{"event_type":"user.created","event_data":{}}'
        sig, kid, ts = sign_test_webhook(body, timestamp=int(time.time()) - 600)

        result = await webhooks_mod._verify_signature(
            body, sig, kid, ts, "https://test.example/.well-known/jwks.json"
        )

        assert result is False

    @patch.object(webhooks_mod, "get_jwk_set", new_callable=AsyncMock)
    async def test_unknown_kid_fails(self, mock_jwks: AsyncMock):
        mock_jwks.return_value = TEST_JWK_SET
        body = b'{"event_type":"user.created","event_data":{}}'
        sig, _kid, ts = sign_test_webhook(body)

        result = await webhooks_mod._verify_signature(
            body, sig, "wrong-kid", ts, "https://test.example/.well-known/jwks.json"
        )

        assert result is False

    @patch.object(webhooks_mod, "get_jwk_set", new_callable=AsyncMock)
    async def test_invalid_jws_format_fails(self, mock_jwks: AsyncMock):
        mock_jwks.return_value = TEST_JWK_SET
        body = b'{"event_type":"user.created","event_data":{}}'

        result = await webhooks_mod._verify_signature(
            body,
            "not-a-jws",
            "kid",
            str(int(time.time())),
            "https://test.example/.well-known/jwks.json",
        )

        assert result is False

    async def test_non_numeric_timestamp_fails(self):
        result = await webhooks_mod._verify_signature(
            b"body",
            "h..s",
            "kid",
            "not-a-number",
            "https://test.example/.well-known/jwks.json",
        )

        assert result is False


# ---------------------------------------------------------------------------
# Event handlers
# ---------------------------------------------------------------------------


class TestUserBeforeCreate:
    """user.before_create event: email allowlist validation."""

    @patch.object(
        webhooks_mod.settings.server, "allowed_emails", "a@mixd.app,b@mixd.app"
    )
    def test_allowed_email_returns_true(self):
        body = _webhook_body("user.before_create", email="a@mixd.app")
        payload = json.loads(body)

        result = webhooks_mod._handle_user_before_create(payload["event_data"])

        assert result == {"allowed": True}

    @patch.object(webhooks_mod.settings.server, "allowed_emails", "a@mixd.app")
    def test_denied_email_returns_false(self):
        body = _webhook_body("user.before_create", email="outsider@evil.com")
        payload = json.loads(body)

        result = webhooks_mod._handle_user_before_create(payload["event_data"])

        assert result["allowed"] is False
        assert "error_message" in result
        assert "error_code" in result

    @patch.object(webhooks_mod.settings.server, "allowed_emails", "")
    def test_no_allowlist_permits_all(self):
        body = _webhook_body("user.before_create", email="anyone@anywhere.com")
        payload = json.loads(body)

        result = webhooks_mod._handle_user_before_create(payload["event_data"])

        assert result == {"allowed": True}


class TestUserCreated:
    """user.created event: logs signup, returns success."""

    def test_returns_success(self):
        body = _webhook_body("user.created", email="new@mixd.app")
        payload = json.loads(body)

        result = webhooks_mod._handle_user_created(payload["event_data"])

        assert result == {"success": True}


# ---------------------------------------------------------------------------
# Endpoint integration (unit-level, no HTTP server)
# ---------------------------------------------------------------------------


def _make_webhook_client():
    """Build a TestClient with the webhook router mounted."""
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    from src.interface.api.routes.webhooks import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


class TestWebhookEndpoint:
    """Full endpoint flow: headers → verify → dispatch → response."""

    @patch.object(webhooks_mod, "get_jwk_set", new_callable=AsyncMock)
    @patch.object(
        webhooks_mod.settings.server, "neon_auth_jwks_url", "https://test.example/jwks"
    )
    async def test_valid_request_dispatches_event(self, mock_jwks: AsyncMock):
        mock_jwks.return_value = TEST_JWK_SET
        client = _make_webhook_client()

        body = _webhook_body("user.created")
        sig, kid, ts = sign_test_webhook(body)

        response = client.post(
            "/webhooks/neon-auth",
            content=body,
            headers={
                "x-neon-signature": sig,
                "x-neon-signature-kid": kid,
                "x-neon-timestamp": ts,
                "content-type": "application/json",
            },
        )

        assert response.status_code == 200
        assert response.json() == {"success": True}

    @patch.object(
        webhooks_mod.settings.server, "neon_auth_jwks_url", "https://test.example/jwks"
    )
    async def test_missing_headers_returns_401(self):
        client = _make_webhook_client()

        response = client.post(
            "/webhooks/neon-auth",
            content=b'{"event_type":"user.created"}',
            headers={"content-type": "application/json"},
        )

        assert response.status_code == 401
        assert response.json()["error"]["code"] == "MISSING_SIGNATURE"

    @patch.object(webhooks_mod.settings.server, "neon_auth_jwks_url", "")
    async def test_unconfigured_returns_503(self):
        client = _make_webhook_client()

        response = client.post(
            "/webhooks/neon-auth",
            content=b"{}",
            headers={"content-type": "application/json"},
        )

        assert response.status_code == 503
        assert response.json()["error"]["code"] == "NOT_CONFIGURED"
