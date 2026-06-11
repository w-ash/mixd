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

from collections.abc import Callable
import json
from typing import cast

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, ValidationError

from src.config import get_logger, settings
from src.domain.entities.shared import JsonDict
from src.interface.api.auth_gate import parse_allowed_emails
from src.interface.api.webhook_verification import verify_signature

logger = get_logger(__name__)
router = APIRouter(tags=["webhooks"])


class _WebhookUser(BaseModel):
    """Typed subset of Neon Auth user fields consumed by webhook handlers."""

    model_config = ConfigDict(extra="ignore")
    id: str = ""
    email: str = ""


class _UserEventData(BaseModel):
    """Typed wrapper for Neon Auth ``event_data`` payloads.

    Both ``user.before_create`` and ``user.created`` events carry a ``user``
    object. Defaults allow graceful handling of partial payloads.
    """

    model_config = ConfigDict(extra="ignore")
    user: _WebhookUser = _WebhookUser()


def _handle_user_before_create(event_data: _UserEventData) -> dict[str, str | bool]:
    """Validate whether a user should be allowed to sign up."""
    allowed = parse_allowed_emails(settings.server.allowed_emails)
    if allowed is None:
        return {"allowed": True}

    email = event_data.user.email
    if email in allowed:
        logger.info("webhook_signup_allowed", email=email)
        return {"allowed": True}

    logger.info("webhook_signup_denied", email=email)
    return {
        "allowed": False,
        "error_message": "Your account is not authorized to access this application",
        "error_code": "SIGNUP_DENIED",
    }


def _handle_user_created(event_data: _UserEventData) -> dict[str, str | bool]:
    """Log a new user signup."""
    logger.info(
        "webhook_user_created",
        user_id=event_data.user.id,
        email=event_data.user.email,
    )
    return {"success": True}


_EventHandler = Callable[[_UserEventData], dict[str, str | bool]]

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

    if not await verify_signature(body, signature, kid, timestamp, jwks_url):
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
        payload = cast("JsonDict", json.loads(body))
    except json.JSONDecodeError:
        return JSONResponse(
            {"error": {"code": "INVALID_BODY", "message": "Invalid JSON body"}},
            status_code=400,
        )

    event_type = str(payload.get("event_type", ""))
    event_data = payload.get("event_data", {})

    handler = _EVENT_HANDLERS.get(event_type)
    if handler is None:
        logger.debug("webhook_unhandled_event", event_type=event_type)
        return JSONResponse({"success": True})

    try:
        validated = _UserEventData.model_validate(event_data)
    except ValidationError as exc:
        logger.warning("webhook_invalid_payload", event_type=event_type, error=str(exc))
        return JSONResponse(
            {"error": {"code": "INVALID_PAYLOAD", "message": "Malformed event data"}},
            status_code=400,
        )

    result = handler(validated)
    return JSONResponse(result)
