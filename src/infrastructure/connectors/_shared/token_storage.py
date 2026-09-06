"""Token storage protocol.

Abstracts credential persistence so connectors work with database-backed
storage (the only in-tree implementation — see ``DatabaseTokenStorage``).

The protocol is intentionally in infrastructure (_shared/), not domain —
token storage is a pure infrastructure concern with no business logic.
"""

from collections.abc import Collection, Mapping
from typing import Final, Protocol, TypedDict

from src.config import get_logger
from src.domain.services.oauth_grant import grant_scopes

logger = get_logger(__name__).bind(service="token_storage")

# ``StoredToken.token_type`` vocabulary — a label naming the kind of credential
# in the row, never a secret. Values must fit ``oauth_tokens.token_type``
# VARCHAR(20). Each connector's own kind lives beside that connector
# (``discogs.token_service``, ``chat.credentials``); the shared kinds are here.
OAUTH2_CREDENTIAL_KIND: Final = "oauth2"
SESSION_CREDENTIAL_KIND: Final = "session"
MUSIC_USER_CREDENTIAL_KIND: Final = "music_user_token"


class StoredToken(TypedDict, total=False):
    """Token data as stored. Fields are optional. Each connector uses its own subset.

    Per-connector field contract:

    - Spotify: access_token, refresh_token, scope.
    - Last.fm: session_key.
    - Apple Music: access_token holds the Music User Token. Set explicit expires_at.
      There is no refresh_token.
    - Tidal: access_token and refresh_token, as a pair.
    - Discogs: a personal access token, stored in access_token
      (token_type "personal_token" — the label must fit VARCHAR(20)).
      An OAuth 1.0a token secret would go in extra_data, if ever needed.
    - extra_data["collection_count"] / extra_data["validated_at"] (Discogs):
      collection size and Unix timestamp cached at connect-time validation
      so the status probe can render "N releases" without a network call.
    - extra_data["favorites_count"] (Tidal): favorites size cached by the
      snapshot so the status probe can render "N favorites" network-free.
    - extra_data["storefront"] (Apple Music): the user's storefront id,
      cached best-effort at connect so catalog calls skip the lookup.
    - extra_data["reauth_required"] (Apple Music): True, stamped best-effort
      when the API rejects the stored Music User Token — the status probe
      derives ``needs_reauth`` from it. Cleared by a fresh connect.
    - extra_data["refreshed_at"]: Unix seconds stamped by every successful
      OAuth refresh (``token_refresh_lock``) so lock entrants can tell a
      just-refreshed token from a stale one.
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

    async def load_tokens(
        self, services: Collection[str], user_id: str
    ) -> Mapping[str, StoredToken | None]:
        """Load one user's tokens for several services in a single read.

        Every requested service is a key; a service with no stored token maps to
        ``None``. For callers that judge a whole set of connectors at once (the
        sync-target list), so the answer costs one connection rather than one
        per connector.
        """
        ...

    async def save_token(
        self, service: str, user_id: str, token_data: StoredToken
    ) -> None:
        """Persist token data for a service and user. Upserts (creates or replaces)."""
        ...

    async def update_extra_data(
        self,
        service: str,
        user_id: str,
        updates: Mapping[str, object],
        *,
        account_name: str | None = None,
    ) -> None:
        """Merge ``updates`` into ``extra_data`` without touching token columns.

        The safe write for cache-style fields (counts, markers, backfills):
        unlike load→mutate→``save_token``, it can never write a stale
        refresh token back over a concurrent rotation. ``account_name``
        optionally rides the same narrow write. No-op when no token row
        exists.
        """
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


async def load_required_access_token(
    storage: TokenStorage,
    service: str,
    user_id: str,
    *,
    missing_error: BaseException | type[BaseException],
) -> str:
    """Load the stored ``access_token`` for a service, raising when absent.

    For connectors whose credential cannot be minted on demand (Apple Music's
    Music User Token, Discogs's personal access token): no stored token, or a
    row without an ``access_token``, raises ``missing_error`` — the
    connector's auth-required exception (instance or class).
    """
    stored = await storage.load_token(service, user_id)
    token = stored.get("access_token") if stored else None
    if not token:
        logger.info(f"No {service} token found — auth required")
        raise missing_error
    return token


def get_token_storage() -> TokenStorage:
    """Return the DatabaseTokenStorage implementation."""
    from src.infrastructure.persistence.repositories.token_storage import (
        DatabaseTokenStorage,
    )

    return DatabaseTokenStorage()
