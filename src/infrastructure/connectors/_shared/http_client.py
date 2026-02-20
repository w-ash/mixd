"""Shared httpx client factories for Spotify and Last.fm API connectors.

Provides lightweight per-call AsyncClient factories with:
- Structured request/response logging via event hooks
- Error response body logging for debugging
- Service-specific timeouts from settings
- Authorization headers baked in for Spotify

Usage:
    async with make_spotify_client(token) as client:
        response = await client.get("/tracks", params={"ids": "...", "market": "US"})
        response.raise_for_status()
        return response.json()
"""

import httpx

from src.config import get_logger, settings

SPOTIFY_API_BASE = "https://api.spotify.com/v1"
SPOTIFY_ACCOUNTS_BASE = "https://accounts.spotify.com"
LASTFM_API_BASE = "https://ws.audioscrobbler.com/2.0"

_http_logger = get_logger(__name__).bind(service="http_client")

_HTTP_ERROR_THRESHOLD = 400


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


async def _log_response(response: httpx.Response) -> None:  # noqa: RUF029 — httpx AsyncClient requires async hooks
    """Log incoming HTTP responses; WARNING level on 4xx/5xx with Retry-After."""
    elapsed_ms = (
        round(response.elapsed.total_seconds() * 1000, 1) if response.elapsed else None
    )
    retry_after = response.headers.get("Retry-After")
    if response.status_code < _HTTP_ERROR_THRESHOLD:
        _http_logger.debug(
            "HTTP response",
            status=response.status_code,
            url=str(response.url),
            elapsed_ms=elapsed_ms,
        )
    else:
        _http_logger.warning(
            "HTTP error response",
            status=response.status_code,
            url=str(response.url),
            elapsed_ms=elapsed_ms,
            retry_after=retry_after,
        )


_EVENT_HOOKS: dict[str, list] = {
    "request": [_log_request],
    "response": [_log_response],
}


# -------------------------------------------------------------------------
# CLIENT FACTORIES
# -------------------------------------------------------------------------


def make_spotify_client(access_token: str) -> httpx.AsyncClient:
    """Return a configured AsyncClient for Spotify Web API calls.

    Uses /v1 as base URL. Authorization Bearer header is pre-set.
    Timeouts sourced from settings.api.spotify_request_timeout.
    """
    return httpx.AsyncClient(
        base_url=SPOTIFY_API_BASE,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        timeout=httpx.Timeout(
            connect=5.0,
            read=float(settings.api.spotify_request_timeout or 15),
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
    Timeouts sourced from settings.api.lastfm_request_timeout.
    """
    return httpx.AsyncClient(
        base_url=LASTFM_API_BASE,
        timeout=httpx.Timeout(
            connect=5.0,
            read=float(settings.api.lastfm_request_timeout or 30),
            write=10.0,
            pool=5.0,
        ),
        event_hooks=_EVENT_HOOKS,
    )


# -------------------------------------------------------------------------
# ERROR LOGGING HELPER
# -------------------------------------------------------------------------


def log_error_response_body(e: httpx.HTTPStatusError, operation: str) -> None:
    """Log the response body from an HTTP error for debugging.

    Call this inside _impl methods after catching HTTPStatusError before re-raising.
    Caps body at 500 characters to avoid flooding logs.

    Args:
        e: The HTTPStatusError with response body
        operation: Name of the operation for log context
    """
    _http_logger.debug(
        "API error response body",
        operation=operation,
        status=e.response.status_code,
        body=e.response.text[:500],
    )
