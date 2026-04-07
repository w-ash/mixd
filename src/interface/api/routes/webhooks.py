"""Neon Auth webhook endpoint.

Receives authentication events from Neon Auth via HTTP POST:
- ``user.before_create`` — validates email against the allowlist (blocking)
- ``user.created`` — logs new user signups (non-blocking)

Signature verification uses EdDSA (Ed25519) detached JWS, validated
against the same JWKS endpoint used for JWT authentication. The webhook
path ``/webhooks/neon-auth`` does not start with ``/api/``, so
NeonAuthMiddleware passes it through without requiring a Bearer token.

See: https://neon.com/guides/neon-auth-webhooks-nextjs
"""

# pyright: reportAny=false
# Legitimate Any: webhook payloads are untyped JSON from external source

import base64
from collections.abc import Callable
import json
import time
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from src.config import get_logger, settings
from src.interface.api.auth_gate import get_jwk_set, parse_allowed_emails

logger = get_logger(__name__)
router = APIRouter(tags=["webhooks"])

_MAX_TIMESTAMP_AGE_SECONDS = 300
_JWS_PART_COUNT = 3


async def _verify_signature(
    body: bytes, signature: str, kid: str, timestamp: str, jwks_url: str
) -> bool:
    """Verify a Neon Auth EdDSA detached JWS signature.

    Neon Auth signs webhooks with Ed25519 using a detached JWS format:
    ``header..signature`` (empty payload section). The actual signing
    input is reconstructed from the base64url-encoded JWS header,
    a period, and the base64url-encoded ``timestamp.body`` payload.
    """
    try:
        ts = int(timestamp)
    except ValueError:
        logger.warning("webhook_invalid_timestamp", timestamp=timestamp)
        return False

    age = abs(time.time() - ts)
    if age > _MAX_TIMESTAMP_AGE_SECONDS:
        logger.warning("webhook_timestamp_too_old", age_seconds=age)
        return False

    parts = signature.split(".")
    if len(parts) != _JWS_PART_COUNT or parts[1]:
        logger.warning("webhook_invalid_jws_format")
        return False

    jws_header_b64 = parts[0]
    jws_signature_b64 = parts[2]

    jwk_set = await get_jwk_set(jwks_url)
    try:
        jwk = jwk_set[kid]
    except KeyError:
        logger.warning("webhook_key_not_found", kid=kid)
        return False

    payload_raw = f"{timestamp}.".encode() + body
    payload_b64 = base64.urlsafe_b64encode(payload_raw).rstrip(b"=").decode()
    signing_input = f"{jws_header_b64}.{payload_b64}".encode()

    # Add padding for base64url decoding (b64url omits trailing '=')
    sig_padded = jws_signature_b64 + "=" * (-len(jws_signature_b64) % 4)
    sig_bytes = base64.urlsafe_b64decode(sig_padded)

    public_key = jwk.key
    if not isinstance(public_key, Ed25519PublicKey):
        logger.warning("webhook_wrong_key_type", key_type=type(public_key).__name__)
        return False

    try:
        public_key.verify(sig_bytes, signing_input)
    except Exception:
        logger.warning("webhook_signature_invalid")
        return False
    else:
        return True


def _handle_user_before_create(event_data: dict[str, Any]) -> dict[str, Any]:
    """Validate whether a user should be allowed to sign up."""
    allowed = parse_allowed_emails(settings.server.allowed_emails)
    if allowed is None:
        return {"allowed": True}

    user = event_data.get("user", {})
    email = user.get("email", "")
    if email in allowed:
        logger.info("webhook_signup_allowed", email=email)
        return {"allowed": True}

    logger.info("webhook_signup_denied", email=email)
    return {
        "allowed": False,
        "error_message": "Your account is not authorized to access this application",
        "error_code": "SIGNUP_DENIED",
    }


def _handle_user_created(event_data: dict[str, Any]) -> dict[str, Any]:
    """Log a new user signup."""
    user = event_data.get("user", {})
    logger.info(
        "webhook_user_created",
        user_id=user.get("id"),
        email=user.get("email"),
    )
    return {"success": True}


_EventHandler = Callable[[dict[str, Any]], dict[str, Any]]

_EVENT_HANDLERS: dict[str, _EventHandler] = {
    "user.before_create": _handle_user_before_create,
    "user.created": _handle_user_created,
}


@router.post("/webhooks/neon-auth")
async def neon_auth_webhook(request: Request) -> JSONResponse:
    """Receive and process Neon Auth webhook events."""
    jwks_url = settings.server.neon_auth_jwks_url
    if not jwks_url:
        return JSONResponse(
            {"error": {"code": "NOT_CONFIGURED", "message": "Webhooks not configured"}},
            status_code=503,
        )

    signature = request.headers.get("x-neon-signature", "")
    kid = request.headers.get("x-neon-signature-kid", "")
    timestamp = request.headers.get("x-neon-timestamp", "")

    if not (signature and kid and timestamp):
        return JSONResponse(
            {
                "error": {
                    "code": "MISSING_SIGNATURE",
                    "message": "Missing signature headers",
                }
            },
            status_code=401,
        )

    body = await request.body()

    if not await _verify_signature(body, signature, kid, timestamp, jwks_url):
        return JSONResponse(
            {
                "error": {
                    "code": "INVALID_SIGNATURE",
                    "message": "Signature verification failed",
                }
            },
            status_code=401,
        )

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return JSONResponse(
            {"error": {"code": "INVALID_BODY", "message": "Invalid JSON body"}},
            status_code=400,
        )

    event_type = payload.get("event_type", "")
    event_data = payload.get("event_data", {})

    handler = _EVENT_HANDLERS.get(event_type)
    if handler is None:
        logger.debug("webhook_unhandled_event", event_type=event_type)
        return JSONResponse({"success": True})

    result = handler(event_data)
    return JSONResponse(result)
