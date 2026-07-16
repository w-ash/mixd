"""Access-token mint and verify for the remote MCP surface.

Mirrors ``auth_gate._decode_jwt``'s validation posture (EdDSA-only, pinned
``iss``/``aud``, required ``exp``+``sub``) but verifies against the local
in-process key: the authorization server and resource server are the same
FastAPI app, so a JWKS HTTP self-fetch would add a failure mode for nothing.
``/.well-known/jwks.json`` still serves the public key for external tooling.
"""

from datetime import UTC, datetime, timedelta
from typing import TypedDict, cast
from uuid import uuid4

import jwt

from src.config import settings
from src.interface.api.oauth.keys import get_signing_material

_ALGORITHM = "EdDSA"


class McpTokenClaims(TypedDict, total=False):
    """Typed subset of mixd-issued MCP access-token claims.

    ``total=False`` mirrors ``auth_gate.JWTClaims`` — only ``exp`` and ``sub``
    are hard-required (enforced by PyJWT ``options={"require": ...}``).
    """

    sub: str
    email: str
    client_id: str
    scope: str
    jti: str
    iss: str
    aud: str
    exp: int
    iat: int


def mint_access_token(
    *, sub: str, email: str, client_id: str, scopes: tuple[str, ...] = ()
) -> str:
    """Mint an audience-bound MCP access token for a consenting user.

    ``sub`` is the Neon Auth user id (drives RLS scoping on the resource
    server); ``email`` feeds the ``ALLOWED_EMAILS`` gate at verify time;
    ``client_id`` records which OAuth client the token was issued to.
    """
    material = get_signing_material()
    cfg = settings.mcp_oauth
    now = datetime.now(UTC)
    claims: dict[str, str | int] = {
        "iss": cfg.issuer_url,
        "aud": cfg.resource_uri,
        "sub": sub,
        "email": email,
        "client_id": client_id,
        "jti": uuid4().hex,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=cfg.access_token_ttl_seconds)).timestamp()),
    }
    if scopes:
        claims["scope"] = " ".join(scopes)
    return jwt.encode(
        claims,
        material.private_key,
        algorithm=_ALGORITHM,
        headers={"kid": material.kid},
    )


def verify_access_token(token: str) -> McpTokenClaims:
    """Validate an MCP access token; raises ``jwt.InvalidTokenError`` variants.

    Enforces signature (local Ed25519 public key), ``aud`` = the canonical
    resource URI (RFC 8707), ``iss`` = the mixd issuer, ``exp``, and the
    presence of ``sub``.
    """
    material = get_signing_material()
    cfg = settings.mcp_oauth
    return cast(
        "McpTokenClaims",
        jwt.decode(
            token,
            material.public_key,
            algorithms=[_ALGORITHM],
            audience=cfg.resource_uri,
            issuer=cfg.issuer_url,
            options={"require": ["exp", "sub"]},
        ),
    )
