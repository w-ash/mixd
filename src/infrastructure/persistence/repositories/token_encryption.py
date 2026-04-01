"""Field-level encryption for sensitive OAuth token fields.

Uses Fernet (AES-128-CBC + HMAC-SHA256) from the ``cryptography`` library.
Encryption is optional — when no key is configured, values pass through
as plaintext. On read, Fernet ciphertext is detected by its ``gAAAAA`` prefix;
plaintext values are returned as-is for seamless migration.
"""

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from src.config import get_logger

logger = get_logger(__name__)

# Fields in StoredToken / DBOAuthToken that contain secrets
SENSITIVE_FIELDS: frozenset[str] = frozenset({
    "access_token",
    "refresh_token",
    "session_key",
})

# Fernet ciphertext always starts with version byte 0x80, which base64-encodes
# to "gAAAAA". OAuth tokens (JWTs, hex strings) never start with this prefix.
_FERNET_PREFIX = "gAAAAA"


@lru_cache(maxsize=1)
def _get_fernet() -> Fernet | None:
    """Return a cached Fernet instance, or None if encryption is not configured."""
    from src.config import settings

    key = settings.security.token_encryption_key.get_secret_value()
    if not key:
        return None
    return Fernet(key.encode())


def encrypt_field(value: str | None) -> str | None:
    """Encrypt a single field value. Returns None unchanged.

    If no encryption key is configured, returns the value as-is (plaintext mode).
    """
    if value is None:
        return None
    fernet = _get_fernet()
    if fernet is None:
        return value
    return fernet.encrypt(value.encode()).decode()


def decrypt_field(value: str | None) -> str | None:
    """Decrypt a single field value. Returns None unchanged.

    Detects plaintext values (migration path): if the value does not
    start with the Fernet prefix, it is returned as-is.
    If decryption fails (wrong key, corrupted data), returns None
    rather than exposing garbled data — the token manager will trigger
    re-authentication as the correct recovery path.
    """
    if value is None:
        return None
    if not value.startswith(_FERNET_PREFIX):
        return value  # Plaintext — not yet encrypted (migration path)
    fernet = _get_fernet()
    if fernet is None:
        logger.warning("Encrypted token found but no encryption key configured")
        return None
    try:
        return fernet.decrypt(value.encode()).decode()
    except InvalidToken:
        logger.error(
            "Failed to decrypt token field — wrong key or corrupted data",
            exc_info=True,
        )
        return None
