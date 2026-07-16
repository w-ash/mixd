"""Unit tests for the remote-MCP token service (v0.9.5 Phase A).

Covers Ed25519 key loading (PEM + base64-wrapped PEM), RFC 7638 kid
derivation, mint/verify roundtrip, and every rejection path the resource
server relies on (wrong audience/issuer/signature, expiry, missing sub).
"""

import base64
from datetime import UTC, datetime, timedelta
import hashlib
import json

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from fastapi.testclient import TestClient
import jwt
from pydantic import SecretStr
import pytest

from src.config import settings
from src.interface.api.oauth.keys import get_signing_material
from src.interface.api.oauth.tokens import mint_access_token, verify_access_token
from src.interface.api.routes.well_known import router as well_known_router

RESOURCE_URI = "https://mixd.me/mcp"


def _generate_pem() -> str:
    return (
        Ed25519PrivateKey
        .generate()
        .private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        .decode()
    )


@pytest.fixture
def signing_pem(monkeypatch: pytest.MonkeyPatch) -> str:
    """Configure settings.mcp_oauth with a fresh Ed25519 key."""
    pem = _generate_pem()
    monkeypatch.setattr(settings.mcp_oauth, "signing_key", SecretStr(pem))
    monkeypatch.setattr(settings.mcp_oauth, "resource_uri", RESOURCE_URI)
    monkeypatch.setattr(settings.mcp_oauth, "issuer", "")
    return pem


class TestSigningMaterial:
    def test_loads_raw_pem(self, signing_pem: str):
        material = get_signing_material()
        assert material.public_jwk["kty"] == "OKP"
        assert material.public_jwk["crv"] == "Ed25519"
        assert material.public_jwk["alg"] == "EdDSA"
        assert material.public_jwk["use"] == "sig"
        assert material.public_jwk["x"]

    def test_base64_wrapped_pem_yields_same_key(
        self, signing_pem: str, monkeypatch: pytest.MonkeyPatch
    ):
        raw_kid = get_signing_material().kid
        wrapped = base64.b64encode(signing_pem.encode()).decode()
        monkeypatch.setattr(settings.mcp_oauth, "signing_key", SecretStr(wrapped))
        assert get_signing_material().kid == raw_kid

    def test_kid_is_rfc7638_thumbprint(self, signing_pem: str):
        material = get_signing_material()
        core = {k: material.public_jwk[k] for k in ("crv", "kty", "x")}
        digest = hashlib.sha256(
            json.dumps(core, separators=(",", ":"), sort_keys=True).encode()
        ).digest()
        expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
        assert material.kid == expected

    def test_empty_key_rejected(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(settings.mcp_oauth, "signing_key", SecretStr(""))
        with pytest.raises(ValueError, match="not configured"):
            get_signing_material()

    def test_garbage_rejected(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(
            settings.mcp_oauth, "signing_key", SecretStr("not a key !!!")
        )
        with pytest.raises(ValueError, match="neither PEM nor valid base64"):
            get_signing_material()

    def test_base64_of_non_pem_rejected(self, monkeypatch: pytest.MonkeyPatch):
        wrapped = base64.b64encode(b"still not a key").decode()
        monkeypatch.setattr(settings.mcp_oauth, "signing_key", SecretStr(wrapped))
        with pytest.raises(ValueError, match="not a PEM document"):
            get_signing_material()

    def test_non_ed25519_key_rejected(self, monkeypatch: pytest.MonkeyPatch):
        ec_pem = (
            ec
            .generate_private_key(ec.SECP256R1())
            .private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
            .decode()
        )
        monkeypatch.setattr(settings.mcp_oauth, "signing_key", SecretStr(ec_pem))
        with pytest.raises(TypeError, match="must be Ed25519"):
            get_signing_material()


class TestMintVerify:
    def test_roundtrip(self, signing_pem: str):
        token = mint_access_token(
            sub="user-123", email="a@b.c", client_id="https://client.example/meta"
        )
        claims = verify_access_token(token)
        assert claims["sub"] == "user-123"
        assert claims["email"] == "a@b.c"
        assert claims["client_id"] == "https://client.example/meta"
        assert claims["aud"] == RESOURCE_URI
        assert claims["iss"] == "https://mixd.me"
        assert claims["jti"]
        assert "scope" not in claims

    def test_scopes_serialize_as_space_joined(self, signing_pem: str):
        token = mint_access_token(
            sub="u", email="e@x.y", client_id="c", scopes=("read", "write")
        )
        assert verify_access_token(token)["scope"] == "read write"

    def test_wrong_audience_rejected(
        self, signing_pem: str, monkeypatch: pytest.MonkeyPatch
    ):
        token = mint_access_token(sub="u", email="e@x.y", client_id="c")
        # Pin the issuer: resource_uri also drives the *derived* issuer, and a
        # moved issuer would trip InvalidIssuerError before the audience check.
        monkeypatch.setattr(settings.mcp_oauth, "issuer", "https://mixd.me")
        monkeypatch.setattr(
            settings.mcp_oauth, "resource_uri", "https://other.example/mcp"
        )
        with pytest.raises(jwt.InvalidAudienceError):
            verify_access_token(token)

    def test_wrong_issuer_rejected(
        self, signing_pem: str, monkeypatch: pytest.MonkeyPatch
    ):
        token = mint_access_token(sub="u", email="e@x.y", client_id="c")
        monkeypatch.setattr(settings.mcp_oauth, "issuer", "https://evil.example")
        with pytest.raises(jwt.InvalidIssuerError):
            verify_access_token(token)

    def test_expired_rejected(self, signing_pem: str):
        material = get_signing_material()
        past = datetime.now(UTC) - timedelta(hours=2)
        token = jwt.encode(
            {
                "iss": settings.mcp_oauth.issuer_url,
                "aud": RESOURCE_URI,
                "sub": "u",
                "exp": int(past.timestamp()),
            },
            material.private_key,
            algorithm="EdDSA",
        )
        with pytest.raises(jwt.ExpiredSignatureError):
            verify_access_token(token)

    def test_missing_sub_rejected(self, signing_pem: str):
        material = get_signing_material()
        future = datetime.now(UTC) + timedelta(hours=1)
        token = jwt.encode(
            {
                "iss": settings.mcp_oauth.issuer_url,
                "aud": RESOURCE_URI,
                "exp": int(future.timestamp()),
            },
            material.private_key,
            algorithm="EdDSA",
        )
        with pytest.raises(jwt.MissingRequiredClaimError):
            verify_access_token(token)

    def test_foreign_signature_rejected(self, signing_pem: str):
        foreign = Ed25519PrivateKey.generate()
        future = datetime.now(UTC) + timedelta(hours=1)
        token = jwt.encode(
            {
                "iss": settings.mcp_oauth.issuer_url,
                "aud": RESOURCE_URI,
                "sub": "u",
                "exp": int(future.timestamp()),
            },
            foreign,
            algorithm="EdDSA",
        )
        with pytest.raises(jwt.InvalidSignatureError):
            verify_access_token(token)


class TestJwksRoute:
    def test_serves_public_jwk(self, signing_pem: str):
        app = FastAPI()
        app.include_router(well_known_router)
        client = TestClient(app)
        resp = client.get("/.well-known/jwks.json")
        assert resp.status_code == 200
        keys = resp.json()["keys"]
        assert len(keys) == 1
        assert keys[0] == dict(get_signing_material().public_jwk)
        # The private component must never appear in the document.
        assert "d" not in keys[0]
