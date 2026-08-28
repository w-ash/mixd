"""Spotify connector status probe and profile identity fetch.

``get_spotify_status`` reads the stored grant and silently refreshes an
expired access token. ``fetch_spotify_profile`` is the best-effort
``GET /me`` identity fetch shared by the probe's backfill and the auth
callbacks (web + CLI).
"""

import asyncio
import time
from typing import cast

import httpx2

from src.config import get_logger
from src.domain.entities.connector import ConnectorAuthError, ConnectorStatus
from src.domain.exceptions import SpotifyReauthRequiredError
from src.infrastructure.connectors._shared.token_storage import (
    StoredToken,
    TokenStorage,
    get_token_storage,
)
from src.infrastructure.connectors.spotify.auth import (
    SpotifyTokenManager,
    missing_scopes,
)

logger = get_logger(__name__).bind(service="connector_status")

SPOTIFY_ME_URL = "https://api.spotify.com/v1/me"
SPOTIFY_ME_TIMEOUT = 5.0

# Lazily built, module-lived client for GET /me probes — one TLS handshake and
# connection pool across status polls instead of one per call. Closed on API
# shutdown by aclose_profile_client() (the anthropic_adapter teardown pattern);
# a CLI process just lets it die with the process. The loop the client was
# built on is cached alongside it so a second in-process event loop never
# reuses a pool bound to a dead loop.
_profile_client: httpx2.AsyncClient | None = None
_profile_client_loop: asyncio.AbstractEventLoop | None = None


def _get_profile_client() -> httpx2.AsyncClient:
    """Return the pooled ``GET /me`` client, building it on first use.

    Rebuilds when the client is closed or was built on a different (now
    likely dead) event loop.
    """
    global _profile_client, _profile_client_loop
    loop = asyncio.get_running_loop()
    if (
        _profile_client is None
        or _profile_client.is_closed
        or _profile_client_loop is not loop
    ):
        # A client from another loop is dropped, not aclose()d — closing it
        # would await on its dead loop; GC reclaims the sockets.
        _profile_client = httpx2.AsyncClient(timeout=SPOTIFY_ME_TIMEOUT, verify=True)
        _profile_client_loop = loop
    return _profile_client


async def aclose_profile_client() -> None:
    """Close the pooled profile client — called on API shutdown."""
    global _profile_client, _profile_client_loop
    if _profile_client is not None:
        await _profile_client.aclose()
        _profile_client = None
        _profile_client_loop = None


async def fetch_spotify_profile(access_token: str) -> tuple[str | None, str | None]:
    """Best-effort fetch of Spotify identity via GET /me: (display_name, account_id).

    Uses the pooled module client — avoids heavy SpotifyAPIClient
    initialization, token manager, and retry policies. ``account_id`` is
    Spotify's designated key for external linkage (added 2026-05);
    development-mode payloads never carry ``email``/``country``/``product``
    (removed 2026-05), and older tokens may predate ``account_id`` entirely —
    both are tolerated as a missing field, never an error. Returns
    ``(None, None)`` on any failure.
    """
    try:
        resp = await _get_profile_client().get(
            SPOTIFY_ME_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        resp.raise_for_status()
        return _parse_me_profile(cast("dict[str, object]", resp.json()))
    except Exception:
        logger.debug("Failed to fetch Spotify profile", exc_info=True)
        return None, None


def _parse_me_profile(data: dict[str, object]) -> tuple[str | None, str | None]:
    """Pull ``(display_name, account_id)`` off a ``GET /me`` payload."""
    name = data.get("display_name") or data.get("id")
    account_id = data.get("account_id")
    return (
        str(name) if name else None,
        str(account_id) if account_id else None,
    )


def stamp_account_id(token: StoredToken, account_id: str | None) -> StoredToken:
    """Merge ``account_id`` into ``token``'s ``extra_data`` without disturbing
    other keys (e.g. ``authorized_at``). No-op (returns ``token`` unchanged)
    when ``account_id`` is falsy.
    """
    if not account_id:
        return token
    extra_data = {**(token.get("extra_data") or {}), "account_id": account_id}
    return cast("StoredToken", {**token, "extra_data": extra_data})


async def _backfill_profile(
    storage: TokenStorage,
    user_id: str,
    base_token: StoredToken,
    access_token: str,
    *,
    display_name: str | None,
    has_account_id: bool,
) -> str | None:
    """Best-effort ``GET /me`` to fill a missing display name or account_id.

    Only fetches when the cache is actually incomplete — a token stored
    before v0.11.2 may have a cached name but no ``account_id``. Some tokens
    can NEVER yield one (older payload shapes): after a successful ``/me``
    that carries no ``account_id``, ``extra_data["account_id_unavailable"]``
    is stamped so later probes short-circuit instead of re-fetching and
    re-writing on every status poll (see the ``StoredToken`` contract). A
    fetch that fails outright stamps nothing — it taught us nothing, and the
    next probe simply tries again.

    Persists only the delta, through the narrow ``update_extra_data`` write
    (plus ``account_name`` when newly learned) — never a full-token upsert,
    which could write a refresh token loaded before a concurrent rotation
    back over the rotated grant. A save failure is swallowed (logged) rather
    than raised, mirroring the Apple storefront best-effort write-back:
    losing the persist just means the next probe tries again, it doesn't
    invalidate what this call learned.
    """
    extra_data = base_token.get("extra_data") or {}
    needs_account_id = not has_account_id and not extra_data.get(
        "account_id_unavailable"
    )
    if display_name and not needs_account_id:
        return display_name

    fetched_name, account_id = await fetch_spotify_profile(access_token)
    resolved_name = display_name or fetched_name

    updates: dict[str, object] = {}
    if needs_account_id:
        if account_id:
            updates["account_id"] = account_id
        elif fetched_name is not None:
            # /me answered (a successful fetch always yields a name) but
            # carried no account_id — this token will never produce one.
            updates["account_id_unavailable"] = True
    new_name = (
        resolved_name
        if resolved_name and resolved_name != base_token.get("account_name")
        else None
    )
    if not updates and new_name is None:
        return resolved_name
    try:
        await storage.update_extra_data(
            "spotify", user_id, updates, account_name=new_name
        )
    except Exception:
        logger.warning("Failed to persist Spotify profile backfill", exc_info=True)
    return resolved_name


async def get_spotify_status(
    user_id: str,
    storage: TokenStorage | None = None,
) -> ConnectorStatus:
    """Check Spotify auth by reading token from storage.

    If the cached token is expired but a refresh_token exists, attempts
    a silent refresh so the frontend sees a fresh expires_at.
    """
    storage = storage or get_token_storage()
    token_data = await storage.load_token("spotify", user_id)

    if token_data is None:
        return ConnectorStatus(name="spotify", auth_method="oauth", connected=False)

    has_refresh = bool(token_data.get("refresh_token"))
    expires_at = token_data.get("expires_at", 0) or 0
    display_name = token_data.get("account_name")
    has_account_id = bool((token_data.get("extra_data") or {}).get("account_id"))
    auth_error: ConnectorAuthError | None = None
    access_token = token_data.get("access_token")
    granted_scope = token_data.get("scope")

    # Two mutually-exclusive paths: expired-needs-refresh vs valid-token-profile-backfill.
    if has_refresh and expires_at < time.time():
        mgr = SpotifyTokenManager(storage=storage, user_id=user_id)
        try:
            refreshed = await mgr.try_silent_refresh()
        except SpotifyReauthRequiredError:
            # The refresh grant itself aged out (6-month window) or was
            # revoked; the dead token is already deleted. Expected credential
            # aging, fixed with one click — mirrors the Apple Music probe:
            # connected=True + reauth_required derives to needs_reauth,
            # never refresh_failed.
            return ConnectorStatus(
                name="spotify",
                auth_method="oauth",
                connected=True,
                account_name=display_name,
                auth_error="reauth_required",
            )
        if refreshed is None:
            # Refresh failed — refresh_token likely revoked or invalid.
            # Surface as an error rather than silently claiming "connected."
            auth_error = "refresh_failed"
        else:
            expires_at = refreshed.get("expires_at", 0)
            # Spotify echoes the original grant on refresh — the refreshed
            # scope is the authoritative one for the gap check below.
            granted_scope = refreshed.get("scope", granted_scope)
            display_name = await _backfill_profile(
                storage,
                user_id,
                cast("StoredToken", refreshed),
                refreshed["access_token"],
                display_name=display_name,
                has_account_id=has_account_id,
            )
    elif (
        has_refresh
        and (not display_name or not has_account_id)
        and isinstance(access_token, str)
        and expires_at > time.time()
    ):
        display_name = await _backfill_profile(
            storage,
            user_id,
            token_data,
            access_token,
            display_name=display_name,
            has_account_id=has_account_id,
        )

    if auth_error is None and missing_scopes(granted_scope):
        auth_error = "scope_missing"

    return ConnectorStatus(
        name="spotify",
        auth_method="oauth",
        # scope_missing is a narrower-grant signal, not a broken session —
        # the connection stays usable for everything already granted.
        connected=has_refresh and auth_error != "refresh_failed",
        account_name=display_name,
        token_expires_at=int(expires_at) if expires_at else None,
        auth_error=auth_error,
    )
