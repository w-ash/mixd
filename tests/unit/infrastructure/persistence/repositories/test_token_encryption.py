"""Tests for field-level token encryption (Fernet)."""

from cryptography.fernet import Fernet
import pytest

from src.infrastructure.persistence.repositories.token_encryption import (
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
    def test_encrypted_output_is_fernet_ciphertext_not_plaintext(self):
        """Fernet tokens start with version byte 0x80, base64 "gAAAAA"; the
        plaintext must not survive anywhere in the stored value."""
        result = encrypt_field("my-secret-token")
        assert result is not None
        assert result.startswith("gAAAAA")
        assert "my-secret-token" not in result


class TestDecryptField:
    """Tests for decrypt_field()."""

    def test_none_passthrough(self):
        assert decrypt_field(None) is None

    def test_plaintext_passthrough(self):
        """Values not starting with Fernet prefix are returned as-is (migration)."""
        assert decrypt_field("plain-oauth-token") == "plain-oauth-token"

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
    @pytest.mark.parametrize(
        "plaintext",
        [
            "my-secret-token",
            "short",
            "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signature",
            "",
        ],
        ids=["token", "short", "jwt", "empty"],
    )
    def test_round_trip(self, plaintext: str):
        assert decrypt_field(encrypt_field(plaintext)) == plaintext


class TestSensitiveFields:
    """Tests for the SENSITIVE_FIELDS constant."""

    def test_contains_expected_fields(self):
        assert {"access_token", "refresh_token", "session_key"} == SENSITIVE_FIELDS
        assert isinstance(SENSITIVE_FIELDS, frozenset)
