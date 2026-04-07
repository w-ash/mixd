"""Shared Ed25519 key pair and JWT/webhook helpers for auth tests.

Generates a test Ed25519 key pair once at import time. Both unit and integration
auth tests import from here to avoid duplicating cryptographic setup.

Uses EdDSA (Ed25519) to match Neon Auth's signing algorithm.
See: https://neon.com/docs/auth/guides/plugins/jwt
"""

import base64
import json
import time

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import jwt

# Generate once at import time — deterministic per test session
_private_key = Ed25519PrivateKey.generate()
_public_key = _private_key.public_key()

# Build a PyJWKSet from the public key (JWKS format: {"keys": [...]})
# kid + use fields are required for PyJWT to match JWT headers to keys
_TEST_KID = "test-key-1"
_jwk_dict = jwt.algorithms.OKPAlgorithm.to_jwk(_public_key, as_dict=True)
_jwk_dict["kid"] = _TEST_KID
_jwk_dict["use"] = "sig"
TEST_JWK_SET = jwt.PyJWKSet.from_dict({"keys": [_jwk_dict]})


_TEST_AUTH_ORIGIN = "https://test.neonauth.example"


def sign_test_jwt(
    claims: dict | None = None,
    *,
    sub: str = "test-user-123",
    email: str = "test@example.com",
    exp_delta: int = 3600,
) -> str:
    """Sign a JWT with the test private key.

    Returns a compact JWS string. Default claims include sub, email, iss,
    aud, and an exp 1 hour in the future — matching Neon Auth's JWT format.
    """
    payload = {
        "sub": sub,
        "email": email,
        "iss": _TEST_AUTH_ORIGIN,
        "aud": _TEST_AUTH_ORIGIN,
        "exp": int(time.time()) + exp_delta,
        "iat": int(time.time()),
    }
    if claims:
        payload.update(claims)
    return jwt.encode(
        payload, _private_key, algorithm="EdDSA", headers={"kid": _TEST_KID}
    )


def sign_test_webhook(
    body: bytes, *, timestamp: int | None = None
) -> tuple[str, str, str]:
    """Create a detached JWS signature for a webhook payload.

    Returns (signature, kid, timestamp) matching the headers Neon Auth sends:
    ``x-neon-signature``, ``x-neon-signature-kid``, ``x-neon-timestamp``.

    The detached JWS format is ``header..signature`` (empty payload section).
    The signing input is ``base64url(header).base64url(timestamp.body)``.
    """
    ts = str(timestamp or int(time.time()))

    # JWS header
    header_dict = {"alg": "EdDSA", "kid": _TEST_KID}
    header_b64 = (
        base64.urlsafe_b64encode(json.dumps(header_dict).encode()).rstrip(b"=").decode()
    )

    # Payload: timestamp.body
    payload_raw = f"{ts}.".encode() + body
    payload_b64 = base64.urlsafe_b64encode(payload_raw).rstrip(b"=").decode()

    # Sign
    signing_input = f"{header_b64}.{payload_b64}".encode()
    sig_bytes = _private_key.sign(signing_input)
    sig_b64 = base64.urlsafe_b64encode(sig_bytes).rstrip(b"=").decode()

    # Detached JWS: header..signature (empty payload)
    detached_jws = f"{header_b64}..{sig_b64}"
    return detached_jws, _TEST_KID, ts
