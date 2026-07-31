"""Shared httpx2 client factories for Spotify, Last.fm, and MusicBrainz API connectors.

Provides AsyncClient factories with:
- Structured request/response logging via event hooks
- Error response body logging for debugging
- Service-specific timeouts from settings

Spotify clients delegate auth to an httpx2.Auth instance (SpotifyBearerAuth)
so token injection and 401-retry are handled transparently.
"""

from collections.abc import Awaitable, Callable
import functools
from typing import cast

import httpx2

from src.config import get_logger, settings
from src.domain.entities.shared import JsonValue

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

    return f"Mixd/{__version__}"


# -------------------------------------------------------------------------
# EVENT HOOKS
# -------------------------------------------------------------------------


async def _log_request(request: httpx2.Request) -> None:  # ruff:ignore[unused-async] — httpx2 AsyncClient requires async hooks
    """Log outgoing HTTP requests at DEBUG level."""
    _http_logger.debug(
        "HTTP request",
        method=request.method,
        url=str(request.url),
    )


def _elapsed_ms(response: httpx2.Response) -> float | None:
    """Return elapsed time in ms, or None if the response hasn't been read yet.

    httpx2 sets ``response.elapsed`` (``_elapsed``) only after the response body
    has been consumed or the connection closed.  Accessing the property before
    that raises ``RuntimeError``, so we guard defensively.
    """
    try:
        return round(response.elapsed.total_seconds() * 1000, 1)
    except RuntimeError:
        return None


async def _log_response(response: httpx2.Response) -> None:
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


type _EventHook = Callable[..., Awaitable[None]]

_EVENT_HOOKS: dict[str, list[_EventHook]] = {
    "request": [_log_request],
    "response": [_log_response],
}


# -------------------------------------------------------------------------
# JSON PARSING BOUNDARY
# -------------------------------------------------------------------------


def parse_json_response(response: httpx2.Response) -> dict[str, JsonValue]:
    """Parse JSON response with typed return.

    httpx2's response.json() returns Any (typeshed #9335, confirmed permanent).
    This helper centralizes the single cast so callers get typed dicts.
    """
    return cast("dict[str, JsonValue]", response.json())


# -------------------------------------------------------------------------
# CLIENT FACTORIES
# -------------------------------------------------------------------------

# Connect/write/pool budgets are identical across every upstream API; only the
# read budget is service-specific, so it stays a per-factory argument.
_CONNECT_TIMEOUT = 5.0
_WRITE_TIMEOUT = 10.0
_POOL_TIMEOUT = 5.0

_MUSICBRAINZ_READ_TIMEOUT = 15.0


def _make_client(
    *,
    base_url: str,
    timeout: httpx2.Timeout,
    auth: httpx2.Auth | None = None,
    headers: dict[str, str] | None = None,
    params: dict[str, str] | None = None,
) -> httpx2.AsyncClient:
    """Build an AsyncClient with the shared logging hooks and TLS verification.

    Every connector client goes through here, so ``_EVENT_HOOKS`` and
    ``verify=True`` are declared once rather than repeated per factory.
    """
    return httpx2.AsyncClient(
        base_url=base_url,
        auth=auth,
        headers=headers,
        params=params,
        timeout=timeout,
        event_hooks=_EVENT_HOOKS,
        verify=True,
    )


def _read_timeout(read: float) -> httpx2.Timeout:
    """Standard timeout profile with a service-specific read budget."""
    return httpx2.Timeout(
        connect=_CONNECT_TIMEOUT,
        read=read,
        write=_WRITE_TIMEOUT,
        pool=_POOL_TIMEOUT,
    )


def make_spotify_client(auth: httpx2.Auth) -> httpx2.AsyncClient:
    """Return a configured AsyncClient for Spotify Web API calls.

    Authentication is delegated to the provided httpx2.Auth instance.
    Caller owns lifecycle — call aclose() or use as async context manager.
    Timeouts sourced from settings.api.spotify.request_timeout.
    """
    return _make_client(
        base_url=SPOTIFY_API_BASE,
        auth=auth,
        headers={"Content-Type": "application/json"},
        timeout=_read_timeout(float(settings.api.spotify.request_timeout)),
    )


def make_spotify_auth_client() -> httpx2.AsyncClient:
    """Return a configured AsyncClient for Spotify OAuth token operations.

    Uses a flat 10s budget on every phase rather than the shared profile — token
    exchange is a single short round trip where a slow connect is as fatal as a
    slow read, so there is nothing to gain from a tighter connect timeout.
    """
    return _make_client(
        base_url=SPOTIFY_ACCOUNTS_BASE,
        timeout=httpx2.Timeout(10.0),
    )


def make_lastfm_client() -> httpx2.AsyncClient:
    """Return a configured AsyncClient for Last.fm API calls.

    Base URL is the /2.0 endpoint. Read-only methods use GET with query params;
    authenticated write methods (track.love, etc.) use POST with form data.
    Timeouts sourced from settings.api.lastfm.request_timeout.
    """
    return _make_client(
        base_url=LASTFM_API_BASE,
        timeout=_read_timeout(float(settings.api.lastfm.request_timeout)),
    )


def make_musicbrainz_client() -> httpx2.AsyncClient:
    """Return a configured AsyncClient for MusicBrainz API calls.

    Base URL is the /ws/2 endpoint. All requests use JSON format via Accept header
    and fmt=json query param. MusicBrainz requires a descriptive User-Agent.
    No authentication needed for read-only requests.
    """
    return _make_client(
        base_url=MUSICBRAINZ_API_BASE,
        headers={
            "Accept": "application/json",
            "User-Agent": _build_user_agent(),
        },
        params={"fmt": "json"},
        timeout=_read_timeout(_MUSICBRAINZ_READ_TIMEOUT),
    )
