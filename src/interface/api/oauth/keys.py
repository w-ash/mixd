"""Ed25519 signing-key handling for the in-app OAuth authorization server.

The key is configured via ``MCP_OAUTH_SIGNING_KEY`` (a Fly secret in
production) as either raw PKCS8 PEM or base64-wrapped PEM. The derived public
JWK is served at ``/.well-known/jwks.json`` and its ``kid`` is the RFC 7638
JWK thumbprint, so key rotation produces a new, stable identifier.
"""

import base64
import binascii
from functools import lru_cache
import hashlib
import json

from attrs import define
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from src.config import settings


@define(frozen=True)
class SigningMaterial:
    """Loaded Ed25519 key pair + its public JWK identity."""

    private_key: Ed25519PrivateKey
    public_key: Ed25519PublicKey
    kid: str
    public_jwk: dict[str, str]


def _b64url(data: bytes) -> str:
    """Base64url without padding (JOSE convention)."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _parse_pem(raw: str) -> bytes:
    """Accept raw PKCS8 PEM or base64-wrapped PEM (Fly-secret friendly)."""
    text = raw.strip()
    if text.startswith("-----BEGIN"):
        return text.encode()
    try:
        decoded = base64.b64decode(text, validate=True)
    except binascii.Error as err:
        raise ValueError(
            "MCP_OAUTH_SIGNING_KEY is neither PEM nor valid base64"
        ) from err
    if not decoded.lstrip().startswith(b"-----BEGIN"):
        raise ValueError("MCP_OAUTH_SIGNING_KEY base64 payload is not a PEM document")
    return decoded


@lru_cache(maxsize=2)
def _material_for(pem: str) -> SigningMaterial:
    key = serialization.load_pem_private_key(_parse_pem(pem), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise TypeError(
            f"MCP OAuth signing key must be Ed25519, got {type(key).__name__}"
        )
    public_key = key.public_key()
    x = public_key.public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    # RFC 7638 thumbprint: SHA-256 over the required members only, in lexical
    # order, with no whitespace — the canonical, rotation-stable key id.
    jwk_core = {"crv": "Ed25519", "kty": "OKP", "x": _b64url(x)}
    thumbprint = hashlib.sha256(
        json.dumps(jwk_core, separators=(",", ":"), sort_keys=True).encode()
    ).digest()
    kid = _b64url(thumbprint)
    public_jwk = {**jwk_core, "kid": kid, "use": "sig", "alg": "EdDSA"}
    return SigningMaterial(
        private_key=key, public_key=public_key, kid=kid, public_jwk=public_jwk
    )


def get_signing_material() -> SigningMaterial:
    """Load and cache the configured signing material.

    Raises ``ValueError`` when no key is configured or the PEM is malformed,
    ``TypeError`` when the key is not Ed25519. ``create_app()`` calls this
    eagerly when the remote-MCP surface is enabled so a bad key fails at
    startup, not on the first token mint.
    """
    pem = settings.mcp_oauth.signing_key.get_secret_value()
    if not pem:
        raise ValueError("MCP_OAUTH_SIGNING_KEY is not configured")
    return _material_for(pem)
