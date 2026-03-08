"""Connector status checking service.

Encapsulates the infrastructure-coupled logic for determining whether
music service connectors are authenticated and healthy — token management,
cache file I/O, and Spotify API calls. Lives in infrastructure because
it contains zero business logic.
"""

# pyright: reportAny=false, reportExplicitAny=false
# Legitimate Any: JSON cache data, httpx response

import json
from pathlib import Path
import time

from attrs import define
import httpx

from src.config import get_logger, settings

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


async def _fetch_spotify_display_name(access_token: str) -> str | None:
    """Best-effort fetch of Spotify display name via GET /me.

    Uses a bare httpx client — avoids heavy SpotifyAPIClient initialization,
    token manager, and retry policies. Returns display_name or user id,
    or None on any error.
    """
    try:
        async with httpx.AsyncClient(timeout=SPOTIFY_ME_TIMEOUT) as client:
            resp = await client.get(
                SPOTIFY_ME_URL,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            resp.raise_for_status()
            data: dict[str, object] = resp.json()
            name = data.get("display_name") or data.get("id")
            return str(name) if name else None
    except Exception:
        logger.opt(exception=True).debug("Failed to fetch Spotify display name")
        return None


async def _try_refresh_spotify_token(
    cache_path: Path,
) -> tuple[int | None, str | None]:
    """Silently refresh an expired Spotify access token.

    Uses SpotifyTokenManager.try_silent_refresh() which handles expired tokens
    without the browser OAuth fallback that would block the server.

    Returns (new_expires_at, display_name) on success, (None, None) on failure.
    """
    from src.infrastructure.connectors.spotify.auth import SpotifyTokenManager

    mgr = SpotifyTokenManager(cache_path=cache_path)
    refreshed = await mgr.try_silent_refresh()
    if refreshed is None:
        return None, None

    display_name = await _fetch_spotify_display_name(refreshed["access_token"])
    return refreshed["expires_at"], display_name


async def get_spotify_status() -> ConnectorStatus:
    """Check Spotify auth by reading .spotify_cache file.

    If the cached token is expired but a refresh_token exists, attempts
    a silent refresh so the frontend sees a fresh expires_at.
    """
    cache_path = Path(".spotify_cache")
    if not cache_path.exists():  # noqa: ASYNC240  # trivial ~200-byte cache file
        return ConnectorStatus(name="spotify", connected=False)

    try:
        token_info: dict[str, object] = json.loads(
            cache_path.read_text(encoding="utf-8")  # noqa: ASYNC240
        )
        # A refresh_token means the connection is persistent — SpotifyTokenManager
        # auto-refreshes expired access tokens, so expires_at alone doesn't
        # determine connectivity.
        has_refresh = bool(token_info.get("refresh_token"))
        raw_expires = token_info.get("expires_at", 0)
        expires_at = (
            float(raw_expires) if isinstance(raw_expires, (int, float, str)) else 0.0
        )

        # Read cached display_name (may have been stored on a previous refresh)
        cached_display_name = token_info.get("display_name")
        display_name = str(cached_display_name) if cached_display_name else None

        # Silent refresh: if token is expired and we have a refresh_token,
        # try to get a fresh access token so the frontend sees "Connected."
        if has_refresh and expires_at < time.time():
            fresh_expires, fresh_display_name = await _try_refresh_spotify_token(
                cache_path
            )
            if fresh_expires is not None:
                expires_at = float(fresh_expires)
            if fresh_display_name:
                display_name = fresh_display_name

        # First visit with valid token but no cached display_name: one-time fetch
        access_token = token_info.get("access_token")
        if (
            has_refresh
            and not display_name
            and isinstance(access_token, str)
            and expires_at > time.time()
        ):
            display_name = await _fetch_spotify_display_name(access_token)
            if display_name:
                # Cache it back so subsequent loads skip the HTTP call
                token_info["display_name"] = display_name
                cache_path.write_text(  # noqa: ASYNC240
                    json.dumps(token_info), encoding="utf-8"
                )

        return ConnectorStatus(
            name="spotify",
            connected=has_refresh,
            account_name=display_name,
            token_expires_at=int(expires_at) if expires_at else None,
        )
    except json.JSONDecodeError, KeyError:
        return ConnectorStatus(name="spotify", connected=False)


def get_lastfm_status() -> ConnectorStatus:
    """Check Last.fm auth by reading credentials from settings."""
    has_key = bool(settings.credentials.lastfm_key)
    has_username = bool(settings.credentials.lastfm_username)
    return ConnectorStatus(
        name="lastfm",
        connected=has_key and has_username,
        account_name=settings.credentials.lastfm_username or None,
    )


def get_musicbrainz_status() -> ConnectorStatus:
    """MusicBrainz is a public API — always available, no auth required."""
    return ConnectorStatus(name="musicbrainz", connected=True)


def get_apple_music_status() -> ConnectorStatus:
    """Apple Music connector is under development — stub status."""
    return ConnectorStatus(name="apple", connected=False)


async def get_all_connector_statuses() -> list[ConnectorStatus]:
    """Get authentication status of all configured connectors."""
    return [
        await get_spotify_status(),
        get_lastfm_status(),
        get_musicbrainz_status(),
        get_apple_music_status(),
    ]
