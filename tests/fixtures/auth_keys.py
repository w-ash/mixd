"""Shared RSA key pair and JWT helpers for auth tests.

Generates a test RSA key pair once at import time. Both unit and integration
auth tests import from here to avoid duplicating cryptographic setup.
"""

import time

from cryptography.hazmat.primitives.asymmetric import rsa
import jwt

# Generate once at import time — deterministic per test session
_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_public_key = _private_key.public_key()

# Build a PyJWKSet from the public key (JWKS format: {"keys": [...]})
# kid + use fields are required for PyJWT to match JWT headers to keys
_TEST_KID = "test-key-1"
_jwk_dict = jwt.algorithms.RSAAlgorithm.to_jwk(_public_key, as_dict=True)  # type: ignore[arg-type]
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
        payload, _private_key, algorithm="RS256", headers={"kid": _TEST_KID}
    )
