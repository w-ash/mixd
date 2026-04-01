"""Tests for field-level token encryption (Fernet)."""

from cryptography.fernet import Fernet
import pytest

from src.infrastructure.persistence.repositories.token_encryption import (
    _FERNET_PREFIX,
    SENSITIVE_FIELDS,
    _get_fernet,
    decrypt_field,
    encrypt_field,
)


@pytest.fixture(autouse=True)
def _clear_fernet_cache():
    """Clear the lru_cache between tests so settings changes take effect."""
    _get_fernet.cache_clear()
    yield
    _get_fernet.cache_clear()


@pytest.fixture
def encryption_key() -> str:
    return Fernet.generate_key().decode()


@pytest.fixture
def _enable_encryption(encryption_key: str, monkeypatch: pytest.MonkeyPatch):
    """Configure a valid encryption key in settings."""
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", encryption_key)
    # Force settings reload by patching the security config directly
    from src.config import settings

    monkeypatch.setattr(
        settings.security,
        "token_encryption_key",
        __import__("pydantic").SecretStr(encryption_key),
    )


class TestEncryptField:
    """Tests for encrypt_field()."""

    def test_none_passthrough(self):
        assert encrypt_field(None) is None

    def test_no_key_returns_plaintext(self, monkeypatch: pytest.MonkeyPatch):
        """Without encryption key, values pass through unchanged."""
        from src.config import settings

        monkeypatch.setattr(
            settings.security,
            "token_encryption_key",
            __import__("pydantic").SecretStr(""),
        )
        assert encrypt_field("my-secret-token") == "my-secret-token"

    @pytest.mark.usefixtures("_enable_encryption")
    def test_encrypted_output_has_fernet_prefix(self):
        result = encrypt_field("my-secret-token")
        assert result is not None
        assert result.startswith(_FERNET_PREFIX)

    @pytest.mark.usefixtures("_enable_encryption")
    def test_encrypted_output_differs_from_input(self):
        result = encrypt_field("my-secret-token")
        assert result != "my-secret-token"


class TestDecryptField:
    """Tests for decrypt_field()."""

    def test_none_passthrough(self):
        assert decrypt_field(None) is None

    def test_plaintext_passthrough(self):
        """Values not starting with Fernet prefix are returned as-is (migration)."""
        assert decrypt_field("plain-oauth-token") == "plain-oauth-token"

    @pytest.mark.usefixtures("_enable_encryption")
    def test_decrypts_encrypted_value(self):
        encrypted = encrypt_field("my-secret-token")
        assert decrypt_field(encrypted) == "my-secret-token"

    def test_encrypted_value_without_key_returns_none(
        self, encryption_key: str, monkeypatch: pytest.MonkeyPatch
    ):
        """If encryption key is removed after encrypting, decrypt returns None."""
        from src.config import settings

        # Encrypt with key
        monkeypatch.setattr(
            settings.security,
            "token_encryption_key",
            __import__("pydantic").SecretStr(encryption_key),
        )
        encrypted = encrypt_field("my-secret-token")

        # Remove key
        _get_fernet.cache_clear()
        monkeypatch.setattr(
            settings.security,
            "token_encryption_key",
            __import__("pydantic").SecretStr(""),
        )
        assert decrypt_field(encrypted) is None

    def test_wrong_key_returns_none(
        self, encryption_key: str, monkeypatch: pytest.MonkeyPatch
    ):
        """Decrypting with wrong key returns None."""
        from src.config import settings

        # Encrypt with original key
        monkeypatch.setattr(
            settings.security,
            "token_encryption_key",
            __import__("pydantic").SecretStr(encryption_key),
        )
        encrypted = encrypt_field("my-secret-token")

        # Decrypt with different key
        _get_fernet.cache_clear()
        other_key = Fernet.generate_key().decode()
        monkeypatch.setattr(
            settings.security,
            "token_encryption_key",
            __import__("pydantic").SecretStr(other_key),
        )
        assert decrypt_field(encrypted) is None


class TestRoundTrip:
    """End-to-end encrypt → decrypt tests."""

    @pytest.mark.usefixtures("_enable_encryption")
    def test_round_trip_short_string(self):
        assert decrypt_field(encrypt_field("short")) == "short"

    @pytest.mark.usefixtures("_enable_encryption")
    def test_round_trip_jwt_like_token(self):
        jwt = (
            "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signature"
        )
        assert decrypt_field(encrypt_field(jwt)) == jwt

    @pytest.mark.usefixtures("_enable_encryption")
    def test_round_trip_empty_string(self):
        assert decrypt_field(encrypt_field("")) == ""


class TestSensitiveFields:
    """Tests for the SENSITIVE_FIELDS constant."""

    def test_contains_expected_fields(self):
        assert {"access_token", "refresh_token", "session_key"} == SENSITIVE_FIELDS

    def test_is_frozenset(self):
        assert isinstance(SENSITIVE_FIELDS, frozenset)
