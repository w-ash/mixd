"""API-only JWT authentication gate via Neon Auth.

Pure ASGI middleware that validates JWT Bearer tokens on ``/api/`` routes
against Neon Auth's JWKS endpoint. Non-API routes (SPA shell, static assets)
pass through unconditionally — page-level auth is handled client-side by
the React ``AuthGuard`` component.

When ``neon_auth_jwks_url`` is empty (local dev), this middleware is never
mounted — see ``app.py``.
"""

import asyncio
import json
import time
from typing import TypedDict, cast
from urllib.parse import urlparse

import httpx
import jwt
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from src.config import get_logger

logger = get_logger(__name__)

# Only /api/ routes require Bearer authentication.
# The SPA shell, static assets, and auth callbacks are served without tokens.
_PROTECTED_PREFIX = "/api/"
_EXEMPT_API_PATHS = ("/api/v1/health",)

# Neon Auth signs JWTs with EdDSA (Ed25519) exclusively.
# See: https://neon.com/docs/auth/guides/plugins/jwt
_ACCEPTED_ALGORITHMS = ["EdDSA"]

# JWKS cache: (parsed key set, fetched_at) + lock to prevent thundering herd
_jwks_cache: tuple[jwt.PyJWKSet | None, float] = (None, 0.0)
_jwks_cache_lock = asyncio.Lock()
_JWKS_CACHE_TTL = 3600  # 1 hour


def parse_allowed_emails(csv: str) -> frozenset[str] | None:
    """Parse a comma-separated email allowlist into a frozenset.

    Returns ``None`` when *csv* is empty, meaning "no restriction".
    Used by both NeonAuthMiddleware (per-request gate) and the Neon Auth
    webhook handler (signup validation).
    """
    if not csv:
        return None
    return frozenset(e.strip() for e in csv.split(",") if e.strip())


async def get_jwk_set(jwks_url: str) -> jwt.PyJWKSet:
    """Fetch, parse, and cache JWKS public keys from Neon Auth.

    Uses a double-check lock to prevent concurrent cache-miss requests
    from all hitting the JWKS endpoint simultaneously.
    """
    global _jwks_cache

    # Fast path: no lock needed when cache is fresh
    jwk_set, fetched_at = _jwks_cache
    if jwk_set is not None and (time.monotonic() - fetched_at) < _JWKS_CACHE_TTL:
        return jwk_set

    # Slow path: single-fetch under lock
    async with _jwks_cache_lock:
        # Double-check: another coroutine may have refreshed while we waited
        jwk_set, fetched_at = _jwks_cache
        if jwk_set is not None and (time.monotonic() - fetched_at) < _JWKS_CACHE_TTL:
            return jwk_set

        async with httpx.AsyncClient(verify=True) as client:
            resp = await client.get(jwks_url, timeout=10)
            resp.raise_for_status()
            jwk_set = jwt.PyJWKSet.from_dict(cast("dict[str, object]", resp.json()))

        _jwks_cache = (jwk_set, time.monotonic())
        return jwk_set


class JWTClaims(TypedDict, total=False):
    """Typed subset of Neon Auth JWT claims consumed by mixd.

    ``total=False`` because JWT claims are optional by nature — only ``sub``
    and ``exp`` are required (enforced by PyJWT ``options={"require": ...}``).
    """

    sub: str
    email: str
    exp: int
    iat: int
    iss: str
    aud: str


def _decode_jwt(
    token: str, jwk_set: jwt.PyJWKSet, *, auth_origin: str | None = None
) -> JWTClaims:
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

    return cast(
        JWTClaims,
        jwt.decode(
            token,
            signing_key,
            algorithms=_ACCEPTED_ALGORITHMS,
            audience=auth_origin,
            issuer=auth_origin,
            options={"require": ["exp", "sub"]},
        ),
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


class NeonAuthMiddleware:
    """ASGI middleware for JWT-based API authentication.

    Only protects ``/api/`` routes. The SPA shell and static assets pass
    through unconditionally — page-level auth is the React client's job.
    """

    def __init__(
        self, app: ASGIApp, jwks_url: str, allowed_emails: frozenset[str] | None = None
    ) -> None:
        self.app = app
        self.jwks_url = jwks_url
        self.allowed_emails = allowed_emails
        # Neon Auth sets aud/iss to the auth service origin
        parsed = urlparse(jwks_url)
        self.auth_origin = f"{parsed.scheme}://{parsed.netloc}"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path: str = cast(str, scope.get("path", ""))

        # Non-API paths pass through (SPA shell, assets, auth callbacks)
        if not path.startswith(_PROTECTED_PREFIX):
            await self.app(scope, receive, send)
            return

        # Exempt API paths (health check)
        if any(path.startswith(exempt) for exempt in _EXEMPT_API_PATHS):
            await self.app(scope, receive, send)
            return

        # Validate Bearer token
        headers = Headers(scope=scope)
        auth_header = headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            try:
                jwk_set = await get_jwk_set(self.jwks_url)
                claims = _decode_jwt(token, jwk_set, auth_origin=self.auth_origin)
            except (jwt.InvalidTokenError, httpx.HTTPError) as exc:
                logger.warning("jwt_validation_failed", error=str(exc))
                await _send_401(send)
                return
            else:
                if self.allowed_emails:
                    email = claims.get("email", "")
                    if not email:
                        logger.warning("jwt_missing_email_claim", sub=claims.get("sub"))
                    if email not in self.allowed_emails:
                        await _send_403(send)
                        return
                scope["auth_user"] = claims
                await self.app(scope, receive, send)
                return

        # No Bearer token on an API route
        await _send_401(send)
