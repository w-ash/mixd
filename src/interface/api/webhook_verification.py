"""Neon Auth webhook signature verification (EdDSA detached JWS).

Lives beside ``auth_gate.py``, which owns the shared JWKS fetching/caching —
webhooks are verified against the same JWKS endpoint used for JWT auth.
"""

import base64
import time
from typing import cast

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from src.config import get_logger
from src.interface.api.auth_gate import get_jwk_set

logger = get_logger(__name__)

_MAX_TIMESTAMP_AGE_SECONDS = 300
_JWS_PART_COUNT = 3


async def verify_signature(
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

    public_key = cast("object", jwk.key)
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
