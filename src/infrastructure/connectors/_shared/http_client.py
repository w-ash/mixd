"""Shared httpx client factories for Spotify, Last.fm, and MusicBrainz API connectors.

Provides AsyncClient factories with:
- Structured request/response logging via event hooks
- Error response body logging for debugging
- Service-specific timeouts from settings

Spotify clients delegate auth to an httpx.Auth instance (SpotifyBearerAuth)
so token injection and 401-retry are handled transparently.
"""

# pyright: reportExplicitAny=false
# Legitimate Any: API response data, framework types

import functools
from typing import Any

import httpx

from src.config import get_logger, settings

SPOTIFY_API_BASE = "https://api.spotify.com/v1"
SPOTIFY_ACCOUNTS_BASE = "https://accounts.spotify.com"
LASTFM_API_BASE = "https://ws.audioscrobbler.com/2.0"
MUSICBRAINZ_API_BASE = "https://musicbrainz.org/ws/2"

_http_logger = get_logger(__name__).bind(service="http_client")

_HTTP_ERROR_THRESHOLD = 400


@functools.cache
def _build_user_agent() -> str:
    """Build User-Agent string. Cached — version never changes at runtime."""
    from src import __version__

    return f"Narada/{__version__}"


# -------------------------------------------------------------------------
# EVENT HOOKS
# -------------------------------------------------------------------------


async def _log_request(request: httpx.Request) -> None:  # noqa: RUF029 — httpx AsyncClient requires async hooks
    """Log outgoing HTTP requests at DEBUG level."""
    _http_logger.debug(
        "HTTP request",
        method=request.method,
        url=str(request.url),
    )


def _elapsed_ms(response: httpx.Response) -> float | None:
    """Return elapsed time in ms, or None if the response hasn't been read yet.

    httpx sets ``response.elapsed`` (``_elapsed``) only after the response body
    has been consumed or the connection closed.  Accessing the property before
    that raises ``RuntimeError``, so we guard defensively.
    """
    try:
        return round(response.elapsed.total_seconds() * 1000, 1)
    except RuntimeError:
        return None


async def _log_response(response: httpx.Response) -> None:
    """Log incoming HTTP responses; WARNING level on 4xx/5xx including buffered body."""
    # aread() is idempotent — buffers the body and populates response._elapsed
    _ = await response.aread()

    if response.status_code < _HTTP_ERROR_THRESHOLD:
        _http_logger.debug(
            "HTTP response",
            status=response.status_code,
            url=str(response.url),
            elapsed_ms=_elapsed_ms(response),
        )
    else:
        _http_logger.warning(
            "HTTP error response",
            status=response.status_code,
            url=str(response.url),
            elapsed_ms=_elapsed_ms(response),
            retry_after=response.headers.get("Retry-After"),
            body=response.text[:500],
        )


_EVENT_HOOKS: dict[str, list[Any]] = {
    "request": [_log_request],
    "response": [_log_response],
}


# -------------------------------------------------------------------------
# CLIENT FACTORIES
# -------------------------------------------------------------------------


def make_spotify_client(auth: httpx.Auth) -> httpx.AsyncClient:
    """Return a configured AsyncClient for Spotify Web API calls.

    Authentication is delegated to the provided httpx.Auth instance.
    Caller owns lifecycle — call aclose() or use as async context manager.
    Timeouts sourced from settings.api.spotify.request_timeout.
    """
    return httpx.AsyncClient(
        base_url=SPOTIFY_API_BASE,
        auth=auth,
        headers={"Content-Type": "application/json"},
        timeout=httpx.Timeout(
            connect=5.0,
            read=float(settings.api.spotify.request_timeout),
            write=10.0,
            pool=5.0,
        ),
        event_hooks=_EVENT_HOOKS,
    )


def make_spotify_auth_client() -> httpx.AsyncClient:
    """Return a configured AsyncClient for Spotify OAuth token operations."""
    return httpx.AsyncClient(
        base_url=SPOTIFY_ACCOUNTS_BASE,
        timeout=httpx.Timeout(10.0),
        event_hooks=_EVENT_HOOKS,
    )


def make_lastfm_client() -> httpx.AsyncClient:
    """Return a configured AsyncClient for Last.fm API calls.

    Base URL is the /2.0 endpoint. Read-only methods use GET with query params;
    authenticated write methods (track.love, etc.) use POST with form data.
    Timeouts sourced from settings.api.lastfm.request_timeout.
    """
    return httpx.AsyncClient(
        base_url=LASTFM_API_BASE,
        timeout=httpx.Timeout(
            connect=5.0,
            read=float(settings.api.lastfm.request_timeout),
            write=10.0,
            pool=5.0,
        ),
        event_hooks=_EVENT_HOOKS,
    )


def make_musicbrainz_client() -> httpx.AsyncClient:
    """Return a configured AsyncClient for MusicBrainz API calls.

    Base URL is the /ws/2 endpoint. All requests use JSON format via Accept header
    and fmt=json query param. MusicBrainz requires a descriptive User-Agent.
    No authentication needed for read-only requests.
    """
    return httpx.AsyncClient(
        base_url=MUSICBRAINZ_API_BASE,
        headers={
            "Accept": "application/json",
            "User-Agent": _build_user_agent(),
        },
        params={"fmt": "json"},
        timeout=httpx.Timeout(
            connect=5.0,
            read=15.0,
            write=10.0,
            pool=5.0,
        ),
        event_hooks=_EVENT_HOOKS,
    )
