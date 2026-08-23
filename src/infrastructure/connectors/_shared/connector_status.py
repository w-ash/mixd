"""Connector status probing.

Provider-specific probes that read credentials from ``TokenStorage`` and
return a domain ``ConnectorStatus`` value object. Each ``get_*_status``
keeps its own flow rather than sharing a template — the auth semantics
diverge enough (Spotify's silent OAuth refresh, Last.fm's api-key +
session-key fallback, stubs that skip auth entirely) that a shared
scaffold would hide the differences that matter for each provider.
"""

import time
from typing import cast

import httpx2

from src.config import get_logger, settings
from src.domain.entities.connector import ConnectorAuthError, ConnectorStatus
from src.domain.exceptions import SpotifyReauthRequiredError
from src.infrastructure.connectors._shared.token_storage import (
    StoredToken,
    TokenStorage,
    get_token_storage,
)

logger = get_logger(__name__).bind(service="connector_status")

SPOTIFY_ME_URL = "https://api.spotify.com/v1/me"
SPOTIFY_ME_TIMEOUT = 5.0


async def fetch_spotify_profile(access_token: str) -> tuple[str | None, str | None]:
    """Best-effort fetch of Spotify identity via GET /me: (display_name, account_id).

    Uses a bare httpx2 client — avoids heavy SpotifyAPIClient initialization,
    token manager, and retry policies. ``account_id`` is Spotify's designated
    key for external linkage (added 2026-05); development-mode payloads never
    carry ``email``/``country``/``product`` (removed 2026-05), and older
    tokens may predate ``account_id`` entirely — both are tolerated as a
    missing field, never an error. Returns ``(None, None)`` on any failure.
    """
    try:
        async with httpx2.AsyncClient(
            timeout=SPOTIFY_ME_TIMEOUT, verify=True
        ) as client:
            resp = await client.get(
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
        from src.infrastructure.connectors.spotify.auth import SpotifyTokenManager

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

    if auth_error is None:
        from src.infrastructure.connectors.spotify.auth import missing_scopes

        if missing_scopes(granted_scope):
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


async def get_lastfm_status(
    user_id: str,
    storage: TokenStorage | None = None,
) -> ConnectorStatus:
    """Check Last.fm auth by looking up stored session key.

    Connected = has stored session key AND has API key configured.
    Falls back to env var check if no session key is stored (password-based
    auth obtains the session key on first authenticated request).
    """
    storage = storage or get_token_storage()
    token_data = await storage.load_token("lastfm", user_id)

    has_api_key = bool(settings.credentials.lastfm_key)
    has_session = token_data is not None and bool(token_data.get("session_key"))
    has_password = bool(
        settings.credentials.lastfm_password
        and settings.credentials.lastfm_password.get_secret_value()
    )
    has_username = bool(settings.credentials.lastfm_username)

    # Connected if we have a stored session key, OR if we have credentials
    # to obtain one (api_key + username + password)
    connected = has_api_key and (has_session or (has_username and has_password))

    account_name = (
        (token_data.get("account_name") if token_data else None)
        or settings.credentials.lastfm_username
        or None
    )

    return ConnectorStatus(
        name="lastfm",
        auth_method="oauth",
        connected=connected,
        account_name=account_name,
    )


async def get_musicbrainz_status(
    user_id: str,
    storage: TokenStorage | None,
) -> ConnectorStatus:
    """MusicBrainz is a public API — always available, no auth required.

    Signature matches the uniform ``status_fn`` shape declared by
    ``ConnectorConfig``; ``user_id`` and ``storage`` are unused.
    """
    del user_id, storage
    return ConnectorStatus(name="musicbrainz", auth_method="none", connected=True)


async def get_apple_music_status(
    user_id: str,
    storage: TokenStorage | None,
) -> ConnectorStatus:
    """Apple Music status from the stored Music User Token — no network calls.

    A MUT cannot be validated without spending a ``/v1/me`` request and cannot
    be refreshed: expiry (fixed ~6-month lifetime) or a recorded
    ``reauth_required`` marker (written by the API client on a 403 rejection)
    both mean the user must re-run the MusicKit browser authorization.
    Mirrors Spotify's convention for expected credential aging: the grant
    stays ``connected=True`` with ``auth_error="reauth_required"`` so the UI
    derives ``needs_reauth`` ("one click to fix"), never ``expired``.
    """
    storage = storage or get_token_storage()
    token_data = await storage.load_token("apple_music", user_id)

    if token_data is None:
        return ConnectorStatus(
            name="apple_music", auth_method="browser_bridge", connected=False
        )

    expires_at = token_data.get("expires_at", 0) or 0
    extra_data = token_data.get("extra_data") or {}
    auth_error: ConnectorAuthError | None = None
    if extra_data.get("reauth_required") or expires_at <= time.time():
        auth_error = "reauth_required"

    return ConnectorStatus(
        name="apple_music",
        auth_method="browser_bridge",
        connected=True,
        # Apple exposes no profile endpoint — there is no account name.
        account_name=None,
        token_expires_at=int(expires_at) if expires_at else None,
        auth_error=auth_error,
    )


async def get_discogs_status(
    user_id: str,
    storage: TokenStorage | None = None,
) -> ConnectorStatus:
    """Discogs status from the stored personal access token — storage only.

    Never a network call (this probe runs on every Integrations render, and
    the whole instance shares one per-IP 60/min Discogs budget): the token
    was validated live at connect time by ``discogs/token_service.py``,
    which also cached ``extra_data["collection_count"]``. The ``detail``
    suffix renders that cached count — a count of 0 still renders
    ("0 releases" is the zero-state invitation to start cataloguing, not an
    error); a token stored without a count yields ``detail=None``.
    """
    storage = storage or get_token_storage()
    token_data = await storage.load_token("discogs", user_id)

    if token_data is None:
        return ConnectorStatus(name="discogs", auth_method="token", connected=False)

    count = (token_data.get("extra_data") or {}).get("collection_count")
    return ConnectorStatus(
        name="discogs",
        auth_method="token",
        connected=True,
        account_name=token_data.get("account_name"),
        detail=f"{count:,} releases" if isinstance(count, int) else None,
    )


async def get_tidal_status(
    user_id: str,
    storage: TokenStorage | None = None,
) -> ConnectorStatus:
    """Tidal status from the stored token pair — storage only, no network.

    The bearer auth refreshes on use, so the probe never spends a refresh
    POST (unlike Spotify's silent-refresh-on-probe — kept cheap on purpose).
    An expired access token with no refresh token to renew it means only
    the reconnect flow helps: ``connected=True`` +
    ``auth_error="reauth_required"`` derives to ``needs_reauth`` (one-click
    fix). No marker check: unlike Apple, no Tidal path records a
    ``reauth_required`` marker — a dead grant is compare-and-deleted on
    ``invalid_grant``, which reads as disconnected here. An expired access
    token *beside* a refresh token is routine — the next API call rotates
    it silently, so the probe reports the stored expiry as-is with no
    error.

    The ``detail`` suffix renders the ``favorites_count`` the snapshot
    cached in ``extra_data`` (the Discogs stored-count pattern) — a count of
    0 still renders ("0 favorites" is the zero-state, not an error); a token
    stored before any snapshot ran yields ``detail=None``.
    """
    storage = storage or get_token_storage()
    token_data = await storage.load_token("tidal", user_id)

    if token_data is None:
        return ConnectorStatus(name="tidal", auth_method="oauth", connected=False)

    expires_at = token_data.get("expires_at", 0) or 0
    extra_data = token_data.get("extra_data") or {}
    has_refresh = bool(token_data.get("refresh_token"))
    auth_error: ConnectorAuthError | None = None
    if not has_refresh and expires_at <= time.time():
        auth_error = "reauth_required"

    count = extra_data.get("favorites_count")
    return ConnectorStatus(
        name="tidal",
        auth_method="oauth",
        connected=True,
        account_name=token_data.get("account_name"),
        token_expires_at=int(expires_at) if expires_at else None,
        auth_error=auth_error,
        detail=f"{count:,} favorites" if isinstance(count, int) else None,
    )


async def get_all_connector_statuses(user_id: str) -> list[ConnectorStatus]:
    """Probe every registered connector concurrently and return their statuses.

    Iterates the discovery registry so adding a connector only requires
    registering a new module — no edits here. Uses ``asyncio.TaskGroup`` for
    structured cancellation: a single status-probe failure surfaces cleanly
    instead of leaking orphaned tasks.
    """
    import asyncio

    # Lazy import avoids a circular dependency:
    # protocols.py imports ConnectorStatus from this module.
    from src.infrastructure.connectors.discovery import discover_connectors

    registry = discover_connectors()
    storage = get_token_storage()

    async with asyncio.TaskGroup() as tg:
        tasks: dict[str, asyncio.Task[ConnectorStatus]] = {
            name: tg.create_task(config["status_fn"](user_id, storage))
            for name, config in registry.items()
        }

    return [tasks[name].result() for name in registry]
