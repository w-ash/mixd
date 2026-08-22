"""Unit tests for the Apple Music developer token provider.

Covers ES256 JWT minting (claims, header), the optional ``origin`` claim,
in-process caching with margin-based re-mint, missing/partial credential
errors, and that key material never leaks through ``repr``/``str``.
"""

from datetime import UTC, datetime, timedelta, tzinfo
from typing import ClassVar, override
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
import jwt
import pytest

from src.config.settings import CredentialsConfig, settings
import src.infrastructure.connectors.apple_music.auth as auth_module
from src.infrastructure.connectors.apple_music.auth import (
    _DEVELOPER_TOKEN_TTL,
    _REMINT_MARGIN,
    DeveloperTokenProvider,
)

TEAM_ID = "TEAM123456"
KEY_ID = "KEYID12345"
ORIGIN = "https://mixd.example.com"


@pytest.fixture(scope="module")
def key_pair() -> tuple[str, str]:
    """Throwaway EC P-256 key pair as (private_pem, public_pem)."""
    private_key = ec.generate_private_key(ec.SECP256R1())
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = (
        private_key
        .public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    return private_pem, public_pem


def _credentials(private_pem: str, *, origin: str = "") -> CredentialsConfig:
    return CredentialsConfig(
        apple_team_id=TEAM_ID,
        apple_key_id=KEY_ID,
        apple_private_key=private_pem,
        apple_music_origin=origin,
    )


@pytest.fixture
def configured(key_pair: tuple[str, str]):
    """Patch settings.credentials with a fully configured Apple key."""
    with patch.object(settings, "credentials", _credentials(key_pair[0])):
        yield


def _decode(token: str, public_pem: str) -> dict[str, object]:
    return jwt.decode(
        token,
        public_pem,
        algorithms=["ES256"],
        options={"verify_signature": True},
    )


class _FakeDatetime(datetime):
    """datetime whose ``now()`` returns a settable fixed instant."""

    fixed_now: ClassVar[datetime]

    @override
    @classmethod
    def now(cls, tz: tzinfo | None = None) -> datetime:
        del tz
        return cls.fixed_now


@pytest.fixture
def fake_clock(monkeypatch: pytest.MonkeyPatch) -> type[_FakeDatetime]:
    _FakeDatetime.fixed_now = datetime(2026, 8, 21, 12, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(auth_module, "datetime", _FakeDatetime)
    return _FakeDatetime


class TestMint:
    """Freshly minted tokens carry the right claims and header."""

    def test_claims_and_header(self, configured: None, key_pair: tuple[str, str]):
        before = datetime.now(UTC)
        token = DeveloperTokenProvider().get_token()
        after = datetime.now(UTC)

        claims = _decode(token, key_pair[1])
        assert claims["iss"] == TEAM_ID
        iat = claims["iat"]
        exp = claims["exp"]
        assert isinstance(iat, int)
        assert isinstance(exp, int)
        assert int(before.timestamp()) <= iat <= int(after.timestamp())
        assert exp - iat == int(_DEVELOPER_TOKEN_TTL.total_seconds())

        header = jwt.get_unverified_header(token)
        assert header["kid"] == KEY_ID
        assert header["alg"] == "ES256"

    def test_origin_claim_absent_when_unconfigured(
        self, configured: None, key_pair: tuple[str, str]
    ):
        token = DeveloperTokenProvider().get_token()
        assert "origin" not in _decode(token, key_pair[1])

    def test_origin_claim_present_as_array_when_configured(
        self, key_pair: tuple[str, str]
    ):
        creds = _credentials(key_pair[0], origin=ORIGIN)
        with patch.object(settings, "credentials", creds):
            token = DeveloperTokenProvider().get_token()
        assert _decode(token, key_pair[1])["origin"] == [ORIGIN]


class TestCache:
    """Tokens are cached in-process and re-minted near expiry."""

    def test_second_call_returns_cached_token(
        self, configured: None, fake_clock: type[_FakeDatetime]
    ):
        provider = DeveloperTokenProvider()
        first = provider.get_token()
        # Advance well within the TTL but past iat resolution, so an
        # accidental re-mint would produce a different token.
        fake_clock.fixed_now += timedelta(days=30)
        assert provider.get_token() == first

    def test_remints_within_margin_of_expiry(
        self, configured: None, fake_clock: type[_FakeDatetime]
    ):
        provider = DeveloperTokenProvider()
        first = provider.get_token()
        fake_clock.fixed_now += (
            _DEVELOPER_TOKEN_TTL - _REMINT_MARGIN + timedelta(hours=1)
        )
        assert provider.get_token() != first


class TestMissingConfig:
    """Missing or partial credentials fail with a clear error."""

    def test_unconfigured_raises(self):
        with (
            patch.object(settings, "credentials", CredentialsConfig()),
            pytest.raises(RuntimeError, match="Apple Music"),
        ):
            _ = DeveloperTokenProvider().get_token()

    def test_partial_config_names_missing_vars(self, key_pair: tuple[str, str]):
        creds = CredentialsConfig(apple_team_id=TEAM_ID, apple_private_key=key_pair[0])
        with (
            patch.object(settings, "credentials", creds),
            pytest.raises(RuntimeError, match="APPLE_KEY_ID"),
        ):
            _ = DeveloperTokenProvider().get_token()


class TestNoKeyLeak:
    """The private key never appears in repr/str of the provider."""

    def test_repr_and_str_contain_no_key_material(
        self, configured: None, key_pair: tuple[str, str]
    ):
        provider = DeveloperTokenProvider()
        _ = provider.get_token()
        key_body = key_pair[0].splitlines()[1]  # first base64 line of the PEM
        for rendered in (repr(provider), str(provider)):
            assert key_body not in rendered
            assert "BEGIN PRIVATE KEY" not in rendered
