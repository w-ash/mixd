"""Connector status checking service.

Encapsulates the infrastructure-coupled logic for determining whether
music service connectors are authenticated and healthy. Uses TokenStorage
for credential persistence (database-backed in production, file-backed
in CLI development).
"""

# pyright: reportAny=false, reportExplicitAny=false
# Legitimate Any: JSON cache data, httpx response

import time

from attrs import define
import httpx

from src.config import get_logger, settings
from src.infrastructure.connectors._shared.token_storage import (
    StoredToken,
    TokenStorage,
    get_token_storage,
)

logger = get_logger(__name__).bind(service="connector_status")

SPOTIFY_ME_URL = "https://api.spotify.com/v1/me"
SPOTIFY_ME_TIMEOUT = 5.0


@define(frozen=True, slots=True)
class ConnectorStatus:
    """Immutable data container for a connector's authentication status."""

    name: str
    connected: bool
    account_name: str | None = None
    token_expires_at: int | None = None


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
            data: dict[str, object] = resp.json()
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
        return ConnectorStatus(name="spotify", connected=False)

    has_refresh = bool(token_data.get("refresh_token"))
    expires_at = token_data.get("expires_at", 0) or 0
    display_name = token_data.get("account_name")

    # Silent refresh: if token is expired and we have a refresh_token,
    # try to get a fresh access token so the frontend sees "Connected."
    if has_refresh and expires_at < time.time():
        from src.infrastructure.connectors.spotify.auth import SpotifyTokenManager

        mgr = SpotifyTokenManager(storage=storage, user_id=user_id)
        refreshed = await mgr.try_silent_refresh()
        if refreshed is not None:
            expires_at = refreshed.get("expires_at", 0)
            if not display_name:
                display_name = await fetch_spotify_display_name(
                    refreshed["access_token"]
                )
                if display_name:
                    # Cache display name back to storage
                    merged: StoredToken = {**refreshed, "account_name": display_name}  # type: ignore[typeddict-item]
                    await storage.save_token("spotify", user_id, merged)

    # First visit with valid token but no cached display_name: one-time fetch
    access_token = token_data.get("access_token")
    if (
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
        connected=has_refresh,
        account_name=display_name,
        token_expires_at=int(expires_at) if expires_at else None,
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
        connected=connected,
        account_name=account_name,
    )


def get_musicbrainz_status() -> ConnectorStatus:
    """MusicBrainz is a public API — always available, no auth required."""
    return ConnectorStatus(name="musicbrainz", connected=True)


def get_apple_music_status() -> ConnectorStatus:
    """Apple Music connector is under development — stub status."""
    return ConnectorStatus(name="apple", connected=False)


async def get_all_connector_statuses(user_id: str) -> list[ConnectorStatus]:
    """Get authentication status of all configured connectors."""
    import asyncio

    storage = get_token_storage()
    results: list[ConnectorStatus] = []
    async with asyncio.TaskGroup() as tg:
        spotify_task = tg.create_task(get_spotify_status(user_id, storage))
        lastfm_task = tg.create_task(get_lastfm_status(user_id, storage))
    results = [
        spotify_task.result(),
        lastfm_task.result(),
        get_musicbrainz_status(),
        get_apple_music_status(),
    ]
    return results
