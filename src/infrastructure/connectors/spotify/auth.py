"""Spotify OAuth 2.0 token manager.

Implements the Authorization Code flow to obtain and refresh access tokens.
Token persistence is delegated to a TokenStorage implementation — either
file-based (CLI) or database-backed (hosted deployment).

Auth flow:
1. Check storage for a valid token — use it if not expired.
2. If token expired, POST to /api/token with refresh_token to get a new one.
3. If no token exists (first run), open browser to /authorize, run a minimal
   local HTTP server to capture the redirect code, then exchange code for tokens.

Thread safety: asyncio.Lock prevents concurrent token refresh storms when
multiple tasks call get_valid_token() simultaneously.
"""

# pyright: reportAny=false

import asyncio
import base64
import collections.abc
from http.server import BaseHTTPRequestHandler, HTTPServer
import secrets
import time
from typing import Any, TypedDict, cast, override
import urllib.parse
import webbrowser

from attrs import define, field
import httpx

from src.config import get_logger, settings
from src.infrastructure.connectors._shared.http_client import make_spotify_auth_client
from src.infrastructure.connectors._shared.token_storage import (
    StoredToken,
    TokenStorage,
)

logger = get_logger(__name__).bind(service="spotify_auth")

SPOTIFY_AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"  # noqa: S105

SPOTIFY_SCOPES = [
    "playlist-modify-public",
    "playlist-modify-private",
    "playlist-read-private",
    "playlist-read-collaborative",
    "user-library-read",
]


class SpotifyTokenCache(TypedDict):
    """Spotipy-compatible token cache format.

    All fields are required because we normalize the raw Spotify API response
    before storing: _refresh_token() always preserves refresh_token and
    computes expires_at.
    """

    access_token: str
    token_type: str
    expires_in: int
    scope: str
    expires_at: int
    refresh_token: str


# -------------------------------------------------------------------------
# TOKEN MANAGER
# -------------------------------------------------------------------------


@define(slots=True)
class SpotifyTokenManager:
    """Async OAuth 2.0 token manager for the Spotify Web API.

    Token persistence is delegated to the injected TokenStorage — file-based
    for CLI usage, database-backed for hosted deployment.

    Example:
        >>> from src.infrastructure.connectors._shared.token_storage import (
        ...     get_token_storage,
        ... )
        >>> mgr = SpotifyTokenManager(storage=get_token_storage())
        >>> token = await mgr.get_valid_token()
    """

    storage: TokenStorage
    user_id: str
    _refresh_lock: asyncio.Lock = field(factory=asyncio.Lock, init=False, repr=False)
    _token_info: SpotifyTokenCache | None = field(default=None, init=False, repr=False)

    # -------------------------------------------------------------------------
    # CACHE HELPERS
    # -------------------------------------------------------------------------

    async def _load_from_storage(self) -> SpotifyTokenCache | None:
        """Load token from storage. Returns None if missing."""
        stored = await self.storage.load_token("spotify", self.user_id)
        if stored is None:
            return None
        return cast(SpotifyTokenCache, stored)

    async def _save_to_storage(self, token_info: SpotifyTokenCache) -> None:
        """Persist token to storage."""
        await self.storage.save_token(
            "spotify", self.user_id, cast(StoredToken, token_info)
        )

    @staticmethod
    def _is_expired(token_info: SpotifyTokenCache) -> bool:
        """Return True if token will expire within 300 seconds (5 minutes).

        The 300-second buffer gives ample headroom against Spotify's server
        clock skew or early invalidation — tokens are valid for 3600s, so
        we still use each token for ~55 minutes before proactively refreshing.
        """
        return int(time.time()) > token_info.get("expires_at", 0) - 300

    # -------------------------------------------------------------------------
    # OAUTH FLOWS
    # -------------------------------------------------------------------------

    def _basic_auth_header(self) -> str:
        """Return Base64-encoded Basic auth header value for client credentials."""
        client_id = settings.credentials.spotify_client_id
        client_secret = settings.credentials.spotify_client_secret.get_secret_value()
        return base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()

    async def _refresh_token(self, refresh_token: str) -> SpotifyTokenCache:
        """Exchange refresh token for new access token via /api/token."""
        async with make_spotify_auth_client() as client:
            response = await client.post(
                "/api/token",
                headers={"Authorization": f"Basic {self._basic_auth_header()}"},
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                },
            )
            _ = response.raise_for_status()
            raw = cast(dict[str, object], response.json())

        # Spotify sometimes omits refresh_token in refresh responses — preserve the old one
        if "refresh_token" not in raw:
            raw["refresh_token"] = refresh_token

        expires_in = raw.get("expires_in", 3600)
        raw["expires_at"] = int(time.time()) + (
            expires_in if isinstance(expires_in, int) else 3600
        )
        logger.debug("Spotify access token refreshed successfully")
        return cast(SpotifyTokenCache, raw)

    def run_browser_auth(self) -> str:
        """Run Authorization Code flow: open browser, capture redirect code.

        Runs a minimal local HTTP server on port 8888 to capture the OAuth
        redirect callback. Blocks until the user authorizes or a request arrives.

        Returns:
            Authorization code from Spotify's redirect callback.

        Raises:
            RuntimeError: If authorization failed or no code was received.
        """
        client_id = settings.credentials.spotify_client_id
        redirect_uri = settings.credentials.spotify_redirect_uri
        state = secrets.token_urlsafe(16)

        params = {
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "scope": " ".join(SPOTIFY_SCOPES),
            "state": state,
        }
        auth_url = f"{SPOTIFY_AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"

        captured: dict[str, str] = {}

        class _CallbackHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                parsed = urllib.parse.urlparse(self.path)
                qs = urllib.parse.parse_qs(parsed.query)
                captured["code"] = qs.get("code", [""])[0]
                captured["state"] = qs.get("state", [""])[0]
                self.send_response(200)
                self.end_headers()
                self.wfile.write(
                    b"Spotify authorization successful. You may close this tab."
                )

            @override
            def log_message(self, format: str, *args: Any) -> None:
                pass  # Suppress HTTP server access logs

        server = HTTPServer(("127.0.0.1", 8888), _CallbackHandler)

        logger.info("Opening Spotify authorization in browser...")
        webbrowser.open(auth_url)
        server.handle_request()  # Block until exactly one request (the callback)
        server.server_close()

        if not captured.get("code"):
            raise RuntimeError(
                "Spotify authorization failed — no authorization code received. Ensure the redirect URI is configured as http://127.0.0.1:8888/callback."
            )

        if captured.get("state") != state:
            raise RuntimeError(
                "Spotify authorization failed — OAuth state mismatch (possible CSRF)."
            )

        logger.debug("Spotify authorization code captured")
        return captured["code"]

    async def exchange_code(
        self, code: str, *, code_verifier: str | None = None
    ) -> SpotifyTokenCache:
        """Exchange authorization code for access + refresh tokens.

        When ``code_verifier`` is provided (PKCE flow), it's included in the
        token exchange so Spotify can verify the code_challenge sent during
        authorization. The CLI flow omits this — PKCE is optional for
        confidential clients that authenticate with a client_secret.
        """
        redirect_uri = settings.credentials.spotify_redirect_uri
        data: dict[str, str] = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
        }
        if code_verifier:
            data["code_verifier"] = code_verifier
        async with make_spotify_auth_client() as client:
            response = await client.post(
                "/api/token",
                headers={"Authorization": f"Basic {self._basic_auth_header()}"},
                data=data,
            )
            _ = response.raise_for_status()
            raw = cast(dict[str, object], response.json())

        expires_in = raw.get("expires_in", 3600)
        raw["expires_at"] = int(time.time()) + (
            expires_in if isinstance(expires_in, int) else 3600
        )
        logger.info("Spotify authorization complete — token obtained")
        return cast(SpotifyTokenCache, raw)

    # -------------------------------------------------------------------------
    # PUBLIC API
    # -------------------------------------------------------------------------

    async def get_valid_token(self) -> str:
        """Return a valid Spotify access token, refreshing or re-authorizing as needed.

        Thread-safe: uses asyncio.Lock to prevent concurrent refresh storms.
        The second caller that arrives during a refresh will wait for the first
        to complete, then reuse the freshly-obtained token.

        Returns:
            Valid access token string.

        Raises:
            httpx.HTTPStatusError: If token refresh request fails.
            RuntimeError: If browser authorization fails.
        """
        async with self._refresh_lock:
            # Load from storage on first call
            if self._token_info is None:
                self._token_info = await self._load_from_storage()

            # Refresh expired token
            if self._token_info and self._is_expired(self._token_info):
                logger.debug("Spotify access token expired — refreshing")
                self._token_info = await self._refresh_token(
                    self._token_info["refresh_token"]
                )
                await self._save_to_storage(self._token_info)

            # First-time: browser authorization flow
            if self._token_info is None:
                logger.info("No Spotify token found — starting browser authorization")
                code = await asyncio.to_thread(self.run_browser_auth)
                self._token_info = await self.exchange_code(code)
                await self._save_to_storage(self._token_info)

            return self._token_info["access_token"]

    async def try_silent_refresh(self) -> SpotifyTokenCache | None:
        """Attempt silent token refresh without browser fallback.

        Unlike get_valid_token() which falls through to browser OAuth when no
        token exists, this method returns None on any failure — safe for
        server-side use where opening a browser would block the event loop.

        Returns refreshed token info on success, None on failure.
        """
        async with self._refresh_lock:
            if self._token_info is None:
                self._token_info = await self._load_from_storage()
            if self._token_info is None:
                return None
            if not self._is_expired(self._token_info):
                return self._token_info
            try:
                self._token_info = await self._refresh_token(
                    self._token_info["refresh_token"]
                )
            except Exception:
                logger.warning("Silent token refresh failed", exc_info=True)
                return None
            else:
                await self._save_to_storage(self._token_info)
                return self._token_info

    async def force_refresh(self) -> str:
        """Force-refresh the access token regardless of its current expiry state.

        Call this after receiving a 401 Unauthorized response so the next
        get_valid_token() call returns a freshly-issued token.

        Returns:
            New valid access token string.

        Raises:
            httpx.HTTPStatusError: If the refresh request fails.
            RuntimeError: If no refresh token is available.
        """
        async with self._refresh_lock:
            if self._token_info is None:
                self._token_info = await self._load_from_storage()
            if self._token_info is None or "refresh_token" not in self._token_info:
                raise RuntimeError(
                    "No refresh token available — re-authorization required"
                )
            logger.debug("Force-refreshing Spotify access token after 401")
            self._token_info = await self._refresh_token(
                self._token_info["refresh_token"]
            )
            await self._save_to_storage(self._token_info)
            return self._token_info["access_token"]


_HTTP_UNAUTHORIZED = 401


# -------------------------------------------------------------------------
# HTTPX AUTH FLOW
# -------------------------------------------------------------------------


class SpotifyBearerAuth(httpx.Auth):
    """httpx async auth flow: injects Bearer token and retries on 401.

    Used with a long-lived AsyncClient so token injection and 401 retry
    are handled transparently without per-call boilerplate in _impl methods.
    """

    _token_manager: SpotifyTokenManager

    def __init__(self, token_manager: SpotifyTokenManager) -> None:
        self._token_manager = token_manager

    @override
    async def async_auth_flow(
        self, request: httpx.Request
    ) -> collections.abc.AsyncGenerator[httpx.Request, httpx.Response]:
        token = await self._token_manager.get_valid_token()
        request.headers["Authorization"] = f"Bearer {token}"
        response = yield request

        if response.status_code == _HTTP_UNAUTHORIZED:
            new_token = await self._token_manager.force_refresh()
            request.headers["Authorization"] = f"Bearer {new_token}"
            yield request
