"""Site-wide JWT authentication gate via Neon Auth.

Pure ASGI middleware that validates JWT Bearer tokens against Neon Auth's
JWKS endpoint. When ``neon_auth_jwks_url`` is empty (local dev), this
middleware is never mounted — see ``app.py``.

Exempt paths (no auth required):
- ``/api/v1/health`` — Fly.io health checker
- ``/auth/`` — OAuth callback routes
- ``/login`` — login page itself
"""

# pyright: reportAny=false
# Legitimate Any: ASGI scope/message dicts are untyped

import json
import time
from typing import Any
from urllib.parse import urlparse

import httpx
import jwt
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from src.config import get_logger

logger = get_logger(__name__)

_EXEMPT_PREFIXES = ("/api/v1/health", "/auth/", "/login", "/assets/", "/favicon")

# Algorithms accepted from Neon Auth JWTs.
# EdDSA is the current default; RS256 kept for backward compatibility.
_ACCEPTED_ALGORITHMS = ["EdDSA", "RS256"]

# JWKS cache: (parsed key set, fetched_at)
_jwks_cache: tuple[jwt.PyJWKSet | None, float] = (None, 0.0)
_JWKS_CACHE_TTL = 3600  # 1 hour


async def _get_jwk_set(jwks_url: str) -> jwt.PyJWKSet:
    """Fetch, parse, and cache JWKS public keys from Neon Auth."""
    global _jwks_cache
    jwk_set, fetched_at = _jwks_cache

    if jwk_set is not None and (time.monotonic() - fetched_at) < _JWKS_CACHE_TTL:
        return jwk_set

    async with httpx.AsyncClient(verify=True) as client:
        resp = await client.get(jwks_url, timeout=10)
        resp.raise_for_status()
        jwk_set = jwt.PyJWKSet.from_dict(resp.json())

    _jwks_cache = (jwk_set, time.monotonic())
    return jwk_set


def _decode_jwt(
    token: str, jwk_set: jwt.PyJWKSet, *, auth_origin: str | None = None
) -> dict[str, Any]:
    """Validate and decode a JWT using a cached JWKS key set.

    Extracts the signing key from the JWKS by matching the ``kid`` header
    claim. Falls back to the sole key when only one is present.

    Neon Auth sets both ``iss`` and ``aud`` to the auth service origin.
    See: https://neon.com/docs/auth/guides/plugins/jwt
    """
    header = jwt.get_unverified_header(token)
    kid = header.get("kid")
    if kid:
        try:
            signing_key = jwk_set[kid]
        except KeyError as err:
            raise jwt.InvalidTokenError(f"No key found for kid: {kid}") from err
    elif len(jwk_set.keys) == 1:
        signing_key = jwk_set.keys[0]
    else:
        raise jwt.InvalidTokenError("No kid in JWT header and multiple keys in JWKS")

    return jwt.decode(
        token,
        signing_key,
        algorithms=_ACCEPTED_ALGORITHMS,
        audience=auth_origin,
        issuer=auth_origin,
        options={"require": ["exp", "sub"]},
    )


async def _send_401(send: Send) -> None:
    """Send a 401 JSON response with WWW-Authenticate challenge."""
    body = json.dumps({
        "error": {
            "code": "UNAUTHORIZED",
            "message": "Authentication required",
        }
    }).encode()

    await send({
        "type": "http.response.start",
        "status": 401,
        "headers": [
            (b"content-type", b"application/json"),
            (b"www-authenticate", b'Bearer realm="mixd"'),
            (b"content-length", str(len(body)).encode()),
        ],
    })
    await send({"type": "http.response.body", "body": body})


async def _send_403(send: Send) -> None:
    """Send a 403 JSON response for unauthorized users."""
    body = json.dumps({
        "error": {
            "code": "FORBIDDEN",
            "message": "Your account is not authorized to access this application",
        }
    }).encode()

    await send({
        "type": "http.response.start",
        "status": 403,
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ],
    })
    await send({"type": "http.response.body", "body": body})


async def _send_redirect(send: Send, location: str) -> None:
    """Send a 302 redirect to the login page."""
    await send({
        "type": "http.response.start",
        "status": 302,
        "headers": [
            (b"location", location.encode()),
            (b"content-length", b"0"),
        ],
    })
    await send({"type": "http.response.body", "body": b""})


def _wants_html(headers: Headers) -> bool:
    """Check if the client is a browser expecting HTML."""
    accept = headers.get("accept", "")
    return "text/html" in accept


class NeonAuthMiddleware:
    """Pure ASGI middleware for JWT-based site authentication.

    Validates Bearer tokens from Neon Auth's JWKS endpoint.
    Browser requests without a token are redirected to /login.
    API requests without a token receive a 401 JSON response.

    Note: Neon Auth session cookies live on the auth service domain
    and are never sent to the app. All auth goes through Bearer tokens
    obtained via ``authClient.token()`` on the frontend.
    """

    def __init__(
        self, app: ASGIApp, jwks_url: str, allowed_emails: frozenset[str] | None = None
    ) -> None:
        self.app = app
        self.jwks_url = jwks_url
        self.allowed_emails = allowed_emails
        # Neon Auth sets aud to the auth service origin
        parsed = urlparse(jwks_url)
        self.audience = f"{parsed.scheme}://{parsed.netloc}"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path: str = scope.get("path", "")

        # Exempt paths — no auth required
        if any(path.startswith(prefix) for prefix in _EXEMPT_PREFIXES):
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)

        auth_header = headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            try:
                jwk_set = await _get_jwk_set(self.jwks_url)
                claims = _decode_jwt(token, jwk_set, auth_origin=self.audience)
            except (jwt.InvalidTokenError, httpx.HTTPError) as exc:
                logger.warning("jwt_validation_failed", error=str(exc))
                await _send_401(send)
                return
            else:
                # Check email allowlist if configured
                if self.allowed_emails:
                    email = claims.get("email", "")
                    if email not in self.allowed_emails:
                        await _send_403(send)
                        return
                # Attach user info to scope for downstream use
                scope["auth_user"] = claims
                await self.app(scope, receive, send)
                return

        if _wants_html(headers):
            await _send_redirect(send, "/login")
        else:
            await _send_401(send)
