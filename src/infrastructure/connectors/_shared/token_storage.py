"""Token storage protocol.

Abstracts credential persistence so connectors work with database-backed
storage (the only in-tree implementation — see ``DatabaseTokenStorage``).

The protocol is intentionally in infrastructure (_shared/), not domain —
token storage is a pure infrastructure concern with no business logic.
"""

from typing import Protocol, TypedDict

from src.domain.services.oauth_grant import grant_scopes


class StoredToken(TypedDict, total=False):
    """Token data as stored. Fields are optional. Each connector uses its own subset.

    Per-connector field contract:

    - Spotify: access_token, refresh_token, scope.
    - Last.fm: session_key.
    - Apple Music: access_token holds the Music User Token. Set explicit expires_at.
      There is no refresh_token.
    - Tidal: access_token and refresh_token, as a pair.
    - Discogs: a personal access token, stored in access_token.
      An OAuth 1.0a token secret would go in extra_data, if ever needed.
    - account_name: display name, set by any connector.
    - extra_data["authorized_at"]: Unix timestamp of the grant. Set it wherever
      grant age matters (e.g. Spotify's refresh-token expiry window).
    - extra_data["account_id"]: Spotify's designated external-linkage id
      (``GET /me``'s ``account_id`` field, added 2026-05) — stamped at grant
      time and backfilled on status probes for tokens that predate it.
    - extra_data["account_id_unavailable"]: True when a successful ``GET /me``
      carried no ``account_id`` (older payload shape) — the status probe's
      backfill stamps it so later probes stop re-fetching/re-upserting a
      token that will never yield one. Cleared implicitly by a fresh grant
      (``exchange_code`` restamps ``extra_data``).
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


class TokenStorageGrantProvider:
    """Reads scopes off stored tokens; hands out no secrets.

    Satisfies ``ConnectorGrantProvider`` (domain) by narrowing a full
    ``TokenStorage`` down to the one question callers are allowed to ask: which
    scopes does this user's stored grant still carry? No token ever escapes.
    """

    def __init__(self, storage: TokenStorage) -> None:
        self._storage = storage

    async def granted_scopes(self, service: str, user_id: str) -> frozenset[str]:
        """Scopes on the stored token for ``service``/``user_id``."""
        token = await self._storage.load_token(service, user_id)
        return grant_scopes(token.get("scope") if token else None)


def get_token_storage() -> TokenStorage:
    """Return the DatabaseTokenStorage implementation."""
    from src.infrastructure.persistence.repositories.token_storage import (
        DatabaseTokenStorage,
    )

    return DatabaseTokenStorage()
