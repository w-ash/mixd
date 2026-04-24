"""Token storage protocol.

Abstracts credential persistence so connectors work with database-backed
storage (the only in-tree implementation — see ``DatabaseTokenStorage``).

The protocol is intentionally in infrastructure (_shared/), not domain —
token storage is a pure infrastructure concern with no business logic.
"""

from typing import Protocol, TypedDict


class StoredToken(TypedDict, total=False):
    """Token data as stored. All fields optional to support both Spotify and Last.fm.

    Spotify uses: access_token, refresh_token, token_type, expires_in, expires_at, scope
    Last.fm uses: session_key
    Both use: account_name
    """

    access_token: str
    refresh_token: str
    session_key: str
    token_type: str
    expires_in: int
    expires_at: int  # Unix timestamp
    scope: str
    account_name: str
    extra_data: dict[str, object]


class TokenStorage(Protocol):
    """Protocol for reading/writing OAuth tokens and session keys.

    All methods require ``user_id`` to scope tokens per-user (v0.6.3).
    """

    async def load_token(self, service: str, user_id: str) -> StoredToken | None:
        """Load stored token for a service and user. Returns None if no token exists."""
        ...

    async def save_token(
        self, service: str, user_id: str, token_data: StoredToken
    ) -> None:
        """Persist token data for a service and user. Upserts (creates or replaces)."""
        ...

    async def delete_token(self, service: str, user_id: str) -> None:
        """Remove stored token for a service and user."""
        ...


def get_token_storage() -> TokenStorage:
    """Return the DatabaseTokenStorage implementation."""
    from src.infrastructure.persistence.repositories.token_storage import (
        DatabaseTokenStorage,
    )

    return DatabaseTokenStorage()
