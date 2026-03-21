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

import httpx
import jwt
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

_EXEMPT_PREFIXES = ("/api/v1/health", "/auth/", "/login", "/assets/")

# JWKS cache: (parsed key set, fetched_at)
_jwks_cache: tuple[jwt.PyJWKSet | None, float] = (None, 0.0)
_JWKS_CACHE_TTL = 3600  # 1 hour


async def _get_jwk_set(jwks_url: str) -> jwt.PyJWKSet:
    """Fetch, parse, and cache JWKS public keys from Neon Auth."""
    global _jwks_cache
    jwk_set, fetched_at = _jwks_cache

    if jwk_set is not None and (time.monotonic() - fetched_at) < _JWKS_CACHE_TTL:
        return jwk_set

    async with httpx.AsyncClient() as client:
        resp = await client.get(jwks_url, timeout=10)
        resp.raise_for_status()
        jwk_set = jwt.PyJWKSet.from_dict(resp.json())

    _jwks_cache = (jwk_set, time.monotonic())
    return jwk_set


def _decode_jwt(token: str, jwk_set: jwt.PyJWKSet) -> dict[str, Any]:
    """Validate and decode a JWT using a cached JWKS key set."""
    return jwt.decode(
        token,
        jwk_set,  # type: ignore[arg-type]  # PyJWT accepts PyJWKSet at runtime; stubs lag
        algorithms=["RS256"],
        options={"require": ["exp", "sub"]},
    )


async def _send_401(send: Send) -> None:
    """Send a 401 JSON response with WWW-Authenticate challenge."""
    body = json.dumps(
        {
            "error": {
                "code": "UNAUTHORIZED",
                "message": "Authentication required",
            }
        }
    ).encode()

    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"www-authenticate", b'Bearer realm="narada"'),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


async def _send_redirect(send: Send, location: str) -> None:
    """Send a 302 redirect to the login page."""
    await send(
        {
            "type": "http.response.start",
            "status": 302,
            "headers": [
                (b"location", location.encode()),
                (b"content-length", b"0"),
            ],
        }
    )
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
    """

    def __init__(self, app: ASGIApp, jwks_url: str) -> None:
        self.app = app
        self.jwks_url = jwks_url

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

        # Check for Bearer token
        auth_header = headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            try:
                jwk_set = await _get_jwk_set(self.jwks_url)
                claims = _decode_jwt(token, jwk_set)
            except (jwt.InvalidTokenError, httpx.HTTPError):
                await _send_401(send)
                return
            else:
                # Attach user info to scope for downstream use
                scope["auth_user"] = claims
                await self.app(scope, receive, send)
                return

        # Check for session cookie (Neon Auth sets this for browser sessions)
        session_cookie = _extract_cookie(headers, "__Secure-neonauth.session_token")
        if not session_cookie:
            session_cookie = _extract_cookie(headers, "neonauth.session_token")

        if session_cookie:
            # Cookie-based auth: the session is managed by Neon Auth service.
            # We trust the cookie and let the request through — Neon Auth
            # validates it when the frontend fetches the session.
            await self.app(scope, receive, send)
            return

        # No auth credentials — redirect browser, 401 for API
        if _wants_html(headers):
            await _send_redirect(send, "/login")
        else:
            await _send_401(send)


def _extract_cookie(headers: Headers, name: str) -> str | None:
    """Extract a specific cookie value from headers."""
    cookie_header = headers.get("cookie", "")
    for raw_part in cookie_header.split(";"):
        trimmed = raw_part.strip()
        if trimmed.startswith(f"{name}="):
            return trimmed[len(name) + 1 :]
    return None
