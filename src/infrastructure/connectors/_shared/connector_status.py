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

import httpx

from src.config import get_logger, settings
from src.domain.entities.connector import ConnectorAuthError, ConnectorStatus
from src.infrastructure.connectors._shared.token_storage import (
    StoredToken,
    TokenStorage,
    get_token_storage,
)

logger = get_logger(__name__).bind(service="connector_status")

SPOTIFY_ME_URL = "https://api.spotify.com/v1/me"
SPOTIFY_ME_TIMEOUT = 5.0


async def fetch_spotify_display_name(access_token: str) -> str | None:
    """Best-effort fetch of Spotify display name via GET /me.

    Uses a bare httpx client — avoids heavy SpotifyAPIClient initialization,
    token manager, and retry policies. Returns display_name or user id,
    or None on any error.
    """
    try:
        async with httpx.AsyncClient(timeout=SPOTIFY_ME_TIMEOUT, verify=True) as client:
            resp = await client.get(
                SPOTIFY_ME_URL,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            resp.raise_for_status()
            data = cast("dict[str, object]", resp.json())
            name = data.get("display_name") or data.get("id")
            return str(name) if name else None
    except Exception:
        logger.debug("Failed to fetch Spotify display name", exc_info=True)
        return None


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
    auth_error: ConnectorAuthError | None = None
    access_token = token_data.get("access_token")

    # Two mutually-exclusive paths: expired-needs-refresh vs valid-token-name-backfill.
    if has_refresh and expires_at < time.time():
        from src.infrastructure.connectors.spotify.auth import SpotifyTokenManager

        mgr = SpotifyTokenManager(storage=storage, user_id=user_id)
        refreshed = await mgr.try_silent_refresh()
        if refreshed is None:
            # Refresh failed — refresh_token likely revoked or invalid.
            # Surface as an error rather than silently claiming "connected."
            auth_error = "refresh_failed"
        else:
            expires_at = refreshed.get("expires_at", 0)
            if not display_name:
                display_name = await fetch_spotify_display_name(
                    refreshed["access_token"]
                )
                if display_name:
                    merged: StoredToken = {**refreshed, "account_name": display_name}
                    await storage.save_token("spotify", user_id, merged)
    elif (
        has_refresh
        and not display_name
        and isinstance(access_token, str)
        and expires_at > time.time()
    ):
        display_name = await fetch_spotify_display_name(access_token)
        if display_name:
            updated: StoredToken = {**token_data, "account_name": display_name}
            await storage.save_token("spotify", user_id, updated)

    return ConnectorStatus(
        name="spotify",
        auth_method="oauth",
        connected=has_refresh and auth_error is None,
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
    """Apple Music connector is under development — stub status."""
    del user_id, storage
    return ConnectorStatus(
        name="apple_music", auth_method="coming_soon", connected=False
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
