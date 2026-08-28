"""Storefront resolution for Apple Music catalog calls.

Every ``/v1/catalog/{storefront}/...`` request needs the user's storefront id.
The MusicKit connect flow best-effort records it on the stored token
(``extra_data["storefront"]`` — see ``interface/api/routes/apple_auth.py``),
so the stored value is the fast path; a live ``GET /v1/me/storefront`` is the
fallback for tokens stored before the lookup succeeded. A successful fallback
writes the id back onto the stored token (best-effort) so the next
resolution takes the fast path.

A successful resolution is also memoized on the client instance
(``cached_storefront``, invalidated with the MUT cache) — batch runs that
resolve once per batch stop re-reading the token row every ~50 ids.

Returns ``None`` rather than raising when no storefront can be determined
(no stored token, revoked authorization, Apple unreachable): callers own the
failure shape — the matching provider fails its batch with MatchFailures, the
inward resolver fails its batch without writing backoff entries.
"""

from src.config import get_logger
from src.domain.exceptions import AppleMusicAuthRequiredError
from src.infrastructure.connectors._shared.token_storage import TokenStorage
from src.infrastructure.connectors.apple_music.client import (
    APPLE_MUSIC_SERVICE,
    AppleMusicAPIClient,
)

logger = get_logger(__name__)


async def resolve_storefront(
    client: AppleMusicAPIClient,
    *,
    storage: TokenStorage | None = None,
    user_id: str | None = None,
) -> str | None:
    """The user's storefront id, from the client memo, stored token, or a live lookup.

    A resolved id is memoized on ``client.cached_storefront``; failed
    resolutions are not, so the next call retries. ``storage``/``user_id``
    default to the shared token storage and the ambient user context; tests
    pass both explicitly.
    """
    memoized = client.cached_storefront
    if memoized:
        return memoized
    if storage is None:
        from src.infrastructure.connectors._shared.token_storage import (
            get_token_storage,
        )

        storage = get_token_storage()
    if user_id is None:
        from src.infrastructure.persistence.database.user_context import (
            get_current_user_id_from_context,
        )

        user_id = get_current_user_id_from_context()

    stored = await storage.load_token(APPLE_MUSIC_SERVICE, user_id)
    extra_data = (stored.get("extra_data") if stored else None) or {}
    storefront = extra_data.get("storefront")
    if isinstance(storefront, str) and storefront:
        client.cached_storefront = storefront
        return storefront

    try:
        live = await client.get_storefront()
    except AppleMusicAuthRequiredError:
        logger.info("No Apple Music storefront: user authorization missing/expired")
        return None
    if live is None:
        logger.warning("Apple Music storefront lookup returned nothing")
        return None
    if stored is not None:
        await _persist_storefront(storage, user_id, live.id)
    client.cached_storefront = live.id
    return live.id


async def _persist_storefront(
    storage: TokenStorage,
    user_id: str,
    storefront_id: str,
) -> None:
    """Best-effort write-back of a fallback-fetched storefront id.

    Recording it on the stored token turns the next resolution into the fast
    path. The narrow ``update_extra_data`` write touches only the cache
    column — never token columns from a stale load. A storage failure must
    not fail the resolution that already succeeded — log and move on (same
    shape as the client's ``_mark_reauth_required``).
    """
    try:
        await storage.update_extra_data(
            APPLE_MUSIC_SERVICE, user_id, {"storefront": storefront_id}
        )
    except Exception:
        logger.warning(
            "Failed to record Apple Music storefront on the stored token",
            exc_info=True,
        )
