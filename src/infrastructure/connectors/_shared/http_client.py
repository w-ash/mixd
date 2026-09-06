"""Shared httpx2 client factories for the service API connectors — one per
``*_API_BASE`` below.

Provides AsyncClient factories with:
- Structured request/response logging via event hooks
- Error response body logging for debugging
- Service-specific timeouts from settings

Spotify clients delegate auth to an httpx2.Auth instance (SpotifyBearerAuth)
so token injection and 401-retry are handled transparently.
"""

from collections.abc import Awaitable, Callable
import functools
import importlib.metadata
from typing import cast

import httpx2

from src.config import get_logger, settings
from src.domain.entities.shared import JsonValue

SPOTIFY_API_BASE = "https://api.spotify.com/v1"
SPOTIFY_ACCOUNTS_BASE = "https://accounts.spotify.com"
LASTFM_API_BASE = "https://ws.audioscrobbler.com/2.0"
MUSICBRAINZ_API_BASE = "https://musicbrainz.org/ws/2"
# No version segment — Apple Music endpoints carry their own /v1 prefix
# (/v1/me/... and /v1/catalog/... diverge above the version).
APPLE_MUSIC_API_BASE = "https://api.music.apple.com"
DISCOGS_API_BASE = "https://api.discogs.com"
# i.discogs.com rides a separate, undocumented rate bucket: the image client
# bypasses the Discogs API queue/limiter and its responses' rate headers are
# meaningless — callers must ignore them.
DISCOGS_IMAGE_BASE = "https://i.discogs.com"
# Tidal splits its surface across three hosts: catalog/library API calls,
# token exchange (auth.tidal.com/v1/oauth2/token), and the browser-facing
# authorize redirect (login.tidal.com/authorize) — the last has no client
# factory since it is a redirect target, never an httpx2 call.
TIDAL_API_BASE = "https://openapi.tidal.com/v2"
TIDAL_AUTH_BASE = "https://auth.tidal.com"
TIDAL_LOGIN_BASE = "https://login.tidal.com"
# ListenBrainz splits its surface across two hosts: the main API (documented
# X-RateLimit-* client-steered limiting) and the MetaBrainz Labs Dataset
# Hoster (spotify-id-from-metadata lives HERE, not on the main host; no rate
# headers at all). Both route through the one "listenbrainz" limiter — see
# make_listenbrainz_client.
LISTENBRAINZ_API_BASE = "https://api.listenbrainz.org"
LISTENBRAINZ_LABS_BASE = "https://labs.api.listenbrainz.org"

_http_logger = get_logger(__name__).bind(service="http_client")

_HTTP_ERROR_THRESHOLD = 400


@functools.cache
def _build_user_agent() -> str:
    """Build User-Agent string. Cached — version never changes at runtime."""
    from src import __version__

    return f"Mixd/{__version__}"


# Fallback when installed package metadata carries no [project.urls] entries
# (e.g. a source checkout imported without an installed distribution).
_REPO_URL_FALLBACK = "https://github.com/w-ash/mixd"


def _repo_url() -> str:
    """Repository URL from the installed metadata's [project.urls], or fallback."""
    try:
        meta = importlib.metadata.metadata("mixd")
    except importlib.metadata.PackageNotFoundError:
        return _REPO_URL_FALLBACK
    # Project-URL entries serialize as "Label, https://..." strings.
    entries = cast("list[str] | None", meta.get_all("Project-URL")) or []
    for entry in entries:
        label, _, url = entry.partition(",")
        if label.strip().lower() == "repository" and url.strip():
            return url.strip()
    return _REPO_URL_FALLBACK


@functools.cache
def _build_user_agent_with_url() -> str:
    """User-Agent with a contact URL: ``Mixd/<version> +<repo-url>``.

    Discogs silently hands generic User-Agents lower rate limits, so its
    clients identify with the repository URL (pyproject ``[project.urls]``
    via installed metadata, module-constant fallback). MusicBrainz keeps the
    plain :func:`_build_user_agent` form.
    """
    return f"{_build_user_agent()} +{_repo_url()}"


# -------------------------------------------------------------------------
# EVENT HOOKS
# -------------------------------------------------------------------------


async def _log_request(request: httpx2.Request) -> None:
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


def response_text(response: httpx2.Response) -> str:
    """Response body text, or empty when the body was never read (streaming)."""
    try:
        return response.text
    except RuntimeError:
        return ""


def parse_json_body(response: httpx2.Response) -> dict[str, JsonValue] | None:
    """Defensively parse a response body as a JSON object, or ``None``.

    For callers probing an *error* body for a specific key (Spotify's
    ``invalid_grant`` refresh rejection, the quota-429 ``reason`` field): a
    body that isn't valid JSON, or parses to something other than an object,
    must read as "key absent" — never raise. Contrast ``parse_json_response``,
    which is for 2xx bodies the caller requires to be objects.
    """
    try:
        body = cast("object", response.json())
    except ValueError:
        return None
    if not isinstance(body, dict):
        return None
    return cast("dict[str, JsonValue]", body)


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


def make_apple_music_client(auth: httpx2.Auth) -> httpx2.AsyncClient:
    """Return a configured AsyncClient for Apple Music API calls.

    Authentication (the developer-token bearer) is delegated to the provided
    httpx2.Auth instance; the per-user Music-User-Token header is injected
    per-call by the client on /v1/me/* requests. Caller owns lifecycle.
    Timeouts sourced from settings.api.apple_music.request_timeout.
    """
    return _make_client(
        base_url=APPLE_MUSIC_API_BASE,
        auth=auth,
        timeout=_read_timeout(float(settings.api.apple_music.request_timeout)),
    )


def make_discogs_client(auth: httpx2.Auth | None = None) -> httpx2.AsyncClient:
    """Return a configured AsyncClient for Discogs API calls.

    ``auth`` is any httpx2.Auth strategy (``DiscogsTokenAuth`` today, OAuth
    1.0a later) — or ``None`` when the client injects auth per-request after
    lazily loading the stored token. Carries the ``+<repo-url>`` User-Agent:
    Discogs gives generic agents silently lower rate limits. Caller owns
    lifecycle. Timeouts sourced from settings.api.discogs.request_timeout.
    """
    return _make_client(
        base_url=DISCOGS_API_BASE,
        auth=auth,
        headers={
            "Accept": "application/json",
            "User-Agent": _build_user_agent_with_url(),
        },
        timeout=_read_timeout(float(settings.api.discogs.request_timeout)),
    )


def make_discogs_image_client() -> httpx2.AsyncClient:
    """Return a bare AsyncClient for i.discogs.com image fetches.

    No auth, and deliberately outside the Discogs API queue and limiter —
    images ride a separate, undocumented bucket, so callers must not route
    fetches through the serialization queue nor feed these responses to
    ``apply_rate_headers``. Caller owns lifecycle.
    """
    return _make_client(
        base_url=DISCOGS_IMAGE_BASE,
        headers={"User-Agent": _build_user_agent_with_url()},
        timeout=_read_timeout(float(settings.api.discogs.request_timeout)),
    )


def make_tidal_client(auth: httpx2.Auth) -> httpx2.AsyncClient:
    """Return a configured AsyncClient for Tidal API (v2, JSON:API) calls.

    Authentication is delegated to the provided httpx2.Auth instance (OAuth
    2.1 + PKCE bearer — added in a later v0.11.3 packet). Plain User-Agent:
    the ``+<repo-url>`` form is a Discogs-specific accommodation for its
    rate-limit-by-agent behavior, which Tidal has no documented equivalent
    of. Caller owns lifecycle. Timeouts sourced from
    settings.api.tidal.request_timeout.
    """
    return _make_client(
        base_url=TIDAL_API_BASE,
        auth=auth,
        headers={"User-Agent": _build_user_agent()},
        timeout=_read_timeout(float(settings.api.tidal.request_timeout)),
    )


def make_tidal_auth_client() -> httpx2.AsyncClient:
    """Return a configured AsyncClient for Tidal OAuth token operations.

    Targets ``auth.tidal.com`` (token exchange/refresh); the browser-facing
    authorize step redirects to ``login.tidal.com`` and needs no client.
    Timeouts sourced from settings.api.tidal.request_timeout, same as the
    catalog client — unlike Spotify's auth client, there is no separate flat
    budget carved out for this connector.
    """
    return _make_client(
        base_url=TIDAL_AUTH_BASE,
        headers={"User-Agent": _build_user_agent()},
        timeout=_read_timeout(float(settings.api.tidal.request_timeout)),
    )


def make_listenbrainz_client(
    base_url: str = LISTENBRAINZ_API_BASE,
) -> httpx2.AsyncClient:
    """Return a configured AsyncClient for ListenBrainz calls on either host.

    Defaults to the main API; pass ``LISTENBRAINZ_LABS_BASE`` for Labs
    (Dataset Hoster) endpoints. One factory rather than one per host because
    both hosts share every setting and the one ``listenbrainz`` limiter —
    uniform pacing protects a free shared MetaBrainz service whose Labs host
    sends no rate headers to self-correct from. No authentication. Caller
    owns lifecycle. Timeouts sourced from settings.api.listenbrainz.
    """
    return _make_client(
        base_url=base_url,
        headers={"User-Agent": _build_user_agent()},
        timeout=_read_timeout(float(settings.api.listenbrainz.request_timeout)),
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
