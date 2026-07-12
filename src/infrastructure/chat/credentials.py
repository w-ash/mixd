"""Per-user Anthropic API-key storage (BYO-key, v0.9.0.1).

Reuses the connector token-storage plumbing instead of inventing a new secret
store: the key lives in the ``oauth_tokens`` table under service ``"anthropic"``
with ``token_type="api_key"``, in the ``access_token`` column — which the
field-level Fernet encryption (:mod:`token_encryption`) already treats as a
secret, and which RLS already scopes per user exactly like an OAuth token.

This module is the single read/write path that both the web routes and the CLI
``mixd assistant`` group share (the v0.6.5 shared-token-access architecture, the
sanctioned exception to "interface data access only via ``execute_use_case``").
"""

from typing import Literal

from src.config.settings import settings
from src.infrastructure.connectors._shared.token_storage import (
    StoredToken,
    get_token_storage,
)

# Service row + token-type marker under which the BYO key is stored.
ANTHROPIC_SERVICE = "anthropic"
_ANTHROPIC_KIND = "api_key"
# Shortest plausible Anthropic key — a paste-error guard, not a security check.
_MIN_KEY_LEN = 20


async def _load_anthropic_token(user_id: str) -> StoredToken | None:
    """Raw stored token row for the user, or None when no row exists.

    A row that fails to decrypt still returns a (non-None) dict *without* an
    ``access_token`` — the distinction the resolver relies on to avoid a silent
    server-key fallback.
    """
    return await get_token_storage().load_token(ANTHROPIC_SERVICE, user_id)


async def load_user_anthropic_key(user_id: str) -> str | None:
    """Return the user's stored (decrypted) Anthropic key, or None if unset."""
    token = await _load_anthropic_token(user_id)
    # A missing row and a row whose key won't decrypt both surface as None.
    return (token.get("access_token") or None) if token else None


async def resolve_chat_credential(
    user_id: str,
) -> tuple[str, Literal["user", "server"]] | None:
    """The single precedence rule for which credential serves a chat turn.

    The user's own key wins; otherwise the deployment-wide server key (local
    dev / single-tenant self-host); otherwise ``None``. A stored row that fails
    to decrypt yields ``None`` (**not** a silent fallback to the shared server
    key) so the user is told to reconnect rather than unknowingly spending on
    the deployment's account.

    Every capability signal derives from this — ``deps.get_llm_client``,
    ``deps.resolve_chat_source``, and the CLI status all call it — so the
    precedence lives in exactly one place.
    """
    token = await _load_anthropic_token(user_id)
    if token is not None:
        key = token.get("access_token")
        return (key, "user") if key else None
    server_key = settings.credentials.anthropic_api_key.get_secret_value()
    return (server_key, "server") if server_key else None


async def save_user_anthropic_key(user_id: str, api_key: str) -> None:
    """Persist (encrypted) the user's Anthropic key, replacing any existing one."""
    await get_token_storage().save_token(
        ANTHROPIC_SERVICE,
        user_id,
        StoredToken(access_token=api_key, token_type=_ANTHROPIC_KIND),
    )


async def delete_user_anthropic_key(user_id: str) -> None:
    """Remove the user's stored Anthropic key (idempotent)."""
    await get_token_storage().delete_token(ANTHROPIC_SERVICE, user_id)


def looks_like_anthropic_key(api_key: str) -> bool:
    """Cheap format sanity check before a network validation call.

    Anthropic Console keys are ``sk-ant-…`` (~108 chars). This is a guard against
    obvious paste errors, not a security check — the authoritative test is
    :func:`~src.infrastructure.chat.anthropic_adapter.validate_anthropic_key`.
    """
    return api_key.startswith("sk-ant-") and len(api_key) >= _MIN_KEY_LEN
