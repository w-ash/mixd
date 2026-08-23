"""Tidal OAuth 2.1 token manager (PKCE public client).

Implements the Authorization Code + PKCE (S256) flow against Tidal's split
auth surface: the browser-facing authorize redirect (``login.tidal.com``)
and the token endpoint (``auth.tidal.com/v1/oauth2/token``). Tidal is a
public client — ``client_id`` travels in the token-request body and there
is no client secret.

Two Tidal-specific rules shape this module (backlog v0.11.3 decisions):

- **Trust the live ``expires_in``** — Tidal documents no token TTL, so the
  stored expiry always comes from the token response, never a hardcoded
  fallback.
- **Refresh is single-flight per (user, service)** — Tidal ROTATES refresh
  tokens, and a concurrent second refresh with the same token reads as
  replay and revokes the grant. Every refresh runs through the shared
  Postgres-advisory-lock guard (``single_flight_token_refresh``): a losing
  entrant adopts the winner's rotated token instead of POSTing, and the
  winner persists the rotated pair through the guard's lock-holding session.
"""

import asyncio
import base64
import collections.abc
import hashlib
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
import secrets
import time
from typing import TYPE_CHECKING, Final, override
import urllib.parse
import webbrowser

from attrs import define, field
import httpx2

from src.config import get_logger, settings
from src.domain.entities.shared import JsonValue
from src.domain.exceptions import (
    TidalAuthRequiredError,
    TidalReauthRequiredError,
)
from src.infrastructure.connectors._shared.http_client import (
    TIDAL_LOGIN_BASE,
    make_tidal_auth_client,
    parse_json_body,
    parse_json_response,
)
from src.infrastructure.connectors._shared.token_storage import (
    StoredToken,
    TokenStorage,
)
from src.infrastructure.connectors.protocols import CreateStateFn
from src.infrastructure.persistence.repositories.token_refresh_lock import (
    single_flight_token_refresh,
)

if TYPE_CHECKING:
    # fastapi (~160ms import) is used only in annotations here; the guard
    # keeps it out of the CLI's connector import path.
    from fastapi import Request

logger = get_logger(__name__).bind(service="tidal_auth")

TIDAL_AUTHORIZE_URL = f"{TIDAL_LOGIN_BASE}/authorize"
# The token endpoint path ("/v1/oauth2/token") is inlined at its two POST
# sites, mirroring Spotify's "/api/token" — a module constant whose name
# says "token" trips the hardcoded-credential lint for a plain URL path.

# Recorded scope set; verify against dashboard at T7 (app registration
# records the dashboard's allowed-scope list in the epic's completion note).
TIDAL_SCOPES: Final[list[str]] = ["collection.read"]

# Refresh this many seconds before the stored expiry — headroom against
# provider clock skew, mirroring Spotify's buffer.
_EXPIRY_BUFFER_SECONDS = 300

# Waiters at the single-flight guard must outlast a healthy winner's
# refresh POST: lock timeout = the configured request timeout + headroom.
_LOCK_TIMEOUT_HEADROOM_SECONDS: Final = 5.0

_HTTP_UNAUTHORIZED = 401


def _compute_pkce_challenge(code_verifier: str) -> str:
    """Compute S256 PKCE code_challenge from a code_verifier (RFC 7636)."""
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


async def build_auth_url(
    user_id: str,
    request: Request,
    create_state: CreateStateFn,
) -> str:
    """Assemble Tidal's OAuth authorization URL with CSRF state + PKCE.

    Called from the generic ``/api/v1/connectors/{service}/auth-url`` route
    via the connector registry. ``create_state`` persists the state row with
    the server-held code verifier (``DBOAuthState``), so the callback can
    recover it for the token exchange — the verifier never reaches the
    browser.
    """
    del request  # Tidal's auth URL doesn't depend on the incoming request
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = _compute_pkce_challenge(code_verifier)
    state = await create_state(user_id, "tidal", code_verifier=code_verifier)
    params = {
        "client_id": settings.credentials.tidal_client_id,
        "response_type": "code",
        "redirect_uri": settings.credentials.tidal_redirect_uri,
        "scope": " ".join(TIDAL_SCOPES),
        "state": state,
        "code_challenge_method": "S256",
        "code_challenge": code_challenge,
    }
    return f"{TIDAL_AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"


def _stored_token_from_response(raw: dict[str, JsonValue]) -> StoredToken:
    """Normalize a Tidal token response into a ``StoredToken``.

    Trusts the live ``expires_in`` — Tidal documents no token TTL, so a
    response without one fails loudly rather than storing an invented
    expiry.
    """
    access_token = raw.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise ValueError("Tidal token response carried no access_token")
    expires_in = raw.get("expires_in")
    if not isinstance(expires_in, int) or expires_in <= 0:
        raise ValueError(
            "Tidal token response carried no positive integer expires_in — "
            "refusing to invent a TTL"
        )
    token = StoredToken(
        access_token=access_token,
        expires_in=expires_in,
        expires_at=int(time.time()) + expires_in,
    )
    refresh_token = raw.get("refresh_token")
    if isinstance(refresh_token, str) and refresh_token:
        token["refresh_token"] = refresh_token
    token_type = raw.get("token_type")
    if isinstance(token_type, str):
        token["token_type"] = token_type
    scope = raw.get("scope")
    if isinstance(scope, str):
        token["scope"] = scope
    return token


async def exchange_code(
    code: str, code_verifier: str, *, redirect_uri: str | None = None
) -> StoredToken:
    """Exchange an authorization code for the access + refresh token pair.

    Public client: ``client_id`` in the body, no secret; the PKCE
    ``code_verifier`` (server-held per attempt) proves possession of the
    challenge sent at authorization. ``redirect_uri`` must echo the one the
    authorize request carried — the web callback (the configured primary)
    by default; the CLI browser fallback passes its loopback URI. Stamps
    ``extra_data["authorized_at"]`` — the original grant moment, carried
    forward verbatim by every refresh.
    """
    async with make_tidal_auth_client() as client:
        response = await client.post(
            "/v1/oauth2/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri or settings.credentials.tidal_redirect_uri,
                "client_id": settings.credentials.tidal_client_id,
                "code_verifier": code_verifier,
            },
        )
        _ = response.raise_for_status()
        raw = parse_json_response(response)

    token = _stored_token_from_response(raw)
    token["extra_data"] = {"authorized_at": int(time.time())}
    logger.info("Tidal authorization complete — token obtained")
    return token


def _is_invalid_grant(response: httpx2.Response) -> bool:
    """True if a refresh-POST body carries ``error: "invalid_grant"``.

    A body that doesn't parse as a JSON object reads as "not invalid_grant"
    — it falls through to the plain HTTPStatusError path.
    """
    body = parse_json_body(response)
    return body is not None and body.get("error") == "invalid_grant"


# -------------------------------------------------------------------------
# TOKEN MANAGER
# -------------------------------------------------------------------------


@define(slots=True)
class TidalTokenManager:
    """Async OAuth 2.1 token manager for the Tidal API.

    Mirrors ``SpotifyTokenManager``'s shape minus the CLI browser server —
    Tidal connects only via the web callback. The in-process
    ``asyncio.Lock`` stops refresh storms within one event loop; the shared
    single-flight guard serializes refreshes across processes, which
    rotation makes mandatory (a doubled POST revokes the grant as replay).
    """

    storage: TokenStorage
    user_id: str
    _refresh_lock: asyncio.Lock = field(factory=asyncio.Lock, init=False, repr=False)
    _token_info: StoredToken | None = field(default=None, init=False, repr=False)

    @staticmethod
    def _is_expired(token_info: StoredToken) -> bool:
        """True if the token expires within ``_EXPIRY_BUFFER_SECONDS``."""
        return (
            int(time.time()) > token_info.get("expires_at", 0) - _EXPIRY_BUFFER_SECONDS
        )

    async def get_valid_token(self) -> str:
        """Return a valid Tidal access token, refreshing if needed.

        Raises:
            TidalAuthRequiredError: No token is stored — connect flow needed.
            TidalReauthRequiredError: The refresh grant is dead (rotated-away,
                revoked, or absent) — reconnect flow needed.
            httpx2.HTTPStatusError: Any other refresh failure, unchanged.
        """
        async with self._refresh_lock:
            if self._token_info is None:
                self._token_info = await self.storage.load_token("tidal", self.user_id)
            if self._token_info is None:
                logger.info("No Tidal token found — auth required")
                raise TidalAuthRequiredError
            if self._is_expired(self._token_info):
                logger.debug("Tidal access token expired — refreshing")
                self._token_info = await self._refreshed_token(self._token_info)
            access_token = self._token_info.get("access_token")
            if not access_token:
                raise TidalAuthRequiredError
            return access_token

    async def force_refresh(self) -> str:
        """Force-refresh the access token after a 401, ignoring stored expiry.

        Under the single-flight guard a concurrent flight's rotation is
        adopted instead of POSTing — the guard's stored-token re-read makes
        "force" safe against replay revocation.
        """
        async with self._refresh_lock:
            if self._token_info is None:
                self._token_info = await self.storage.load_token("tidal", self.user_id)
            if self._token_info is None:
                raise TidalAuthRequiredError
            logger.debug("Force-refreshing Tidal access token after 401")
            self._token_info = await self._refreshed_token(self._token_info)
            access_token = self._token_info.get("access_token")
            if not access_token:
                raise TidalAuthRequiredError
            return access_token

    async def _refreshed_token(self, current: StoredToken) -> StoredToken:
        """Refresh ``current`` through the shared single-flight guard.

        The losing entrant of a concurrent flight adopts the winner's
        rotated token (no POST); the winner POSTs and persists the rotated
        pair via ``guard.save`` — through the lock-holding session, so the
        rotation lands before the lock releases.
        """
        refresh_token = current.get("refresh_token")
        if not refresh_token:
            # An expired access token with nothing to renew it: only the
            # reconnect flow can help.
            raise TidalReauthRequiredError
        async with single_flight_token_refresh(
            "tidal",
            self.user_id,
            current_refresh_token=refresh_token,
            lock_timeout_seconds=settings.api.tidal.request_timeout
            + _LOCK_TIMEOUT_HEADROOM_SECONDS,
        ) as guard:
            if guard.rotated_token is not None:
                return guard.rotated_token
            new_token = await self._post_refresh(refresh_token, current)
            await guard.save(new_token)
            return new_token

    async def _post_refresh(
        self, refresh_token: str, current: StoredToken
    ) -> StoredToken:
        """POST the refresh_token grant; normalize and carry our fields forward.

        Raises:
            TidalReauthRequiredError: On HTTP 400 ``invalid_grant`` — the
                grant is dead and can never succeed again. The stored token
                is compare-and-deleted first (only when it still carries the
                refresh token that failed, so a stale flight never destroys
                a newer grant).
            httpx2.HTTPStatusError: On any other non-2xx response, unchanged.
        """
        async with make_tidal_auth_client() as client:
            response = await client.post(
                "/v1/oauth2/token",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": settings.credentials.tidal_client_id,
                },
            )
            if response.status_code == HTTPStatus.BAD_REQUEST and _is_invalid_grant(
                response
            ):
                logger.warning(
                    "Tidal refresh rejected with invalid_grant — grant "
                    "revoked or rotated away; reauthorization required"
                )
                stored = await self.storage.load_token("tidal", self.user_id)
                if stored is not None and stored.get("refresh_token") == refresh_token:
                    await self.storage.delete_token("tidal", self.user_id)
                else:
                    logger.info(
                        "Skipping dead-token deletion — stored refresh token "
                        "differs from the one that failed (a newer grant exists)"
                    )
                self._token_info = None
                raise TidalReauthRequiredError
            _ = response.raise_for_status()
            raw = parse_json_response(response)

        new_token = _stored_token_from_response(raw)
        # Rotation should always return a new refresh token; tolerate an
        # omission by keeping the old one rather than storing a pair with
        # no way to renew it.
        if "refresh_token" not in new_token:
            new_token["refresh_token"] = refresh_token
        # `scope` may be omitted on refresh — losing it would read
        # downstream as "the grant covers nothing" for an intact grant.
        if "scope" not in new_token and (previous_scope := current.get("scope")):
            new_token["scope"] = previous_scope
        # extra_data and account_name are ours, never Tidal's — carry them
        # forward verbatim so authorized_at and the cached display name
        # survive every rotation.
        if (previous_extra := current.get("extra_data")) is not None:
            new_token["extra_data"] = previous_extra
        if (previous_name := current.get("account_name")) is not None:
            new_token["account_name"] = previous_name
        logger.debug("Tidal access token refreshed successfully")
        return new_token


# -------------------------------------------------------------------------
# HTTPX2 AUTH FLOW
# -------------------------------------------------------------------------


class TidalBearerAuth(httpx2.Auth):
    """httpx2 async auth flow: injects Bearer token and retries once on 401.

    Used with a long-lived AsyncClient so token injection and the
    one-forced-refresh-then-replay are handled transparently, mirroring
    ``SpotifyBearerAuth``.
    """

    _token_manager: TidalTokenManager

    def __init__(self, token_manager: TidalTokenManager) -> None:
        self._token_manager = token_manager

    @override
    async def async_auth_flow(
        self, request: httpx2.Request
    ) -> collections.abc.AsyncGenerator[httpx2.Request, httpx2.Response]:
        token = await self._token_manager.get_valid_token()
        request.headers["Authorization"] = f"Bearer {token}"
        response = yield request

        if response.status_code == _HTTP_UNAUTHORIZED:
            new_token = await self._token_manager.force_refresh()
            request.headers["Authorization"] = f"Bearer {new_token}"
            yield request


# -------------------------------------------------------------------------
# CLI AUTH FLOWS (v0.11.3 T6)
#
# Device-code flow (RFC 8628) is the primary CLI path — but Tidal's
# ``POST /v1/oauth2/device_authorization`` endpoint is UNDOCUMENTED, so the
# flow is built to fail *typed* (DeviceCodeUnsupportedError) when the
# endpoint turns out not to exist, letting the CLI auto-fall back to the
# localhost-redirect browser flow. The T7 live probe decides which survives.
# -------------------------------------------------------------------------

_DEVICE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"
_SLOW_DOWN_INCREMENT_SECONDS = 5
# The device_authorization response *should* carry expires_in and interval,
# but the endpoint is undocumented — poll-pacing defaults (RFC 8628's
# interval default is 5s) beat refusing an otherwise-workable grant.
_DEVICE_DEFAULT_INTERVAL_SECONDS = 5
_DEVICE_DEFAULT_EXPIRES_SECONDS = 300

# 400 error codes that read as "this endpoint doesn't speak device flow" —
# safe to auto-fall back on. A named *flow* error (authorization_pending,
# invalid_client, invalid_request, …) means the endpoint exists, parsed the
# request, and answered; those SURFACE instead of triggering the fallback —
# in particular invalid_request signals a bug in the request we sent, which
# a silent fallback would mask.
_UNSUPPORTED_ERROR_CODES = frozenset({
    "unsupported_grant_type",
    "unsupported_response_type",
})


class DeviceCodeUnsupportedError(Exception):
    """Tidal's undocumented device_authorization endpoint is not available.

    Raised on a 404, or a 400 whose error code says the request shape itself
    is unsupported — the signal for the CLI to fall back to the
    localhost-redirect browser flow.
    """

    def __init__(self) -> None:
        super().__init__(
            "Tidal's device_authorization endpoint is unavailable — the "
            "device-code flow is not supported by this API surface."
        )


class DeviceCodeExpiredError(Exception):
    """The device authorization expired before the user approved it."""

    def __init__(self) -> None:
        super().__init__(
            "The Tidal device authorization expired before it was approved "
            "— run `mixd tidal auth` again for a fresh code."
        )


@define(frozen=True, slots=True)
class DeviceAuthorization:
    """One device_authorization grant: what the user sees + how we poll."""

    device_code: str
    user_code: str
    verification_uri: str
    verification_uri_complete: str | None
    expires_in: int
    interval: int


def _oauth_error_code(response: httpx2.Response) -> str | None:
    """The ``error`` code from an OAuth error body, if one parses out."""
    body = parse_json_body(response)
    if body is None:
        return None
    error = body.get("error")
    return error if isinstance(error, str) else None


def _required_str(raw: dict[str, JsonValue], key: str) -> str:
    """A required non-empty string field, or a loud ValueError."""
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Tidal device_authorization response was missing {key}")
    return value


def _parse_device_authorization(raw: dict[str, JsonValue]) -> DeviceAuthorization:
    """Normalize the device_authorization response, defaulting poll pacing."""
    device_code = _required_str(raw, "device_code")
    user_code = _required_str(raw, "user_code")
    verification_uri = _required_str(raw, "verification_uri")
    complete = raw.get("verification_uri_complete")
    expires_in = raw.get("expires_in")
    interval = raw.get("interval")
    return DeviceAuthorization(
        device_code=device_code,
        user_code=user_code,
        verification_uri=verification_uri,
        verification_uri_complete=complete if isinstance(complete, str) else None,
        expires_in=(
            expires_in
            if isinstance(expires_in, int) and expires_in > 0
            else _DEVICE_DEFAULT_EXPIRES_SECONDS
        ),
        interval=(
            interval
            if isinstance(interval, int) and interval > 0
            else _DEVICE_DEFAULT_INTERVAL_SECONDS
        ),
    )


async def _poll_device_token(
    client: httpx2.AsyncClient,
    grant: DeviceAuthorization,
    *,
    sleep: collections.abc.Callable[[float], collections.abc.Awaitable[None]],
) -> StoredToken:
    """Poll the token endpoint until approval, expiry, or denial.

    ``authorization_pending`` keeps polling at the current interval;
    ``slow_down`` widens the interval by 5s (RFC 8628 §3.5); the grant's
    ``expires_in`` bounds the loop as a local deadline so a server that
    never says ``expired_token`` still cannot poll forever.
    """
    interval = float(grant.interval)
    deadline = time.monotonic() + grant.expires_in
    while True:
        await sleep(interval)
        if time.monotonic() > deadline:
            raise DeviceCodeExpiredError
        response = await client.post(
            "/v1/oauth2/token",
            data={
                "grant_type": _DEVICE_GRANT_TYPE,
                "device_code": grant.device_code,
                "client_id": settings.credentials.tidal_client_id,
            },
        )
        if response.is_success:
            return _stored_token_from_response(parse_json_response(response))
        error = _oauth_error_code(response)
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            interval += _SLOW_DOWN_INCREMENT_SECONDS
            continue
        if error == "expired_token":
            raise DeviceCodeExpiredError
        if error == "access_denied":
            raise RuntimeError(
                "Tidal authorization was denied — the request was rejected "
                "on the verification page."
            )
        _ = response.raise_for_status()
        raise RuntimeError(
            "Unexpected Tidal device token response "
            f"(HTTP {response.status_code}, no recognized OAuth error)"
        )


async def run_device_auth(
    storage: TokenStorage,
    user_id: str,
    *,
    on_verification: (
        collections.abc.Callable[[DeviceAuthorization], None] | None
    ) = None,
    sleep: collections.abc.Callable[
        [float], collections.abc.Awaitable[None]
    ] = asyncio.sleep,
) -> StoredToken:
    """Run the device-code flow end to end and persist the token pair.

    POSTs device_authorization, hands the user-facing code + URI to
    ``on_verification`` (the CLI renders it — infrastructure owns no
    console), then polls the token endpoint. On success the token is built
    exactly like ``exchange_code``'s (pair + live-``expires_in`` expiry +
    ``extra_data["authorized_at"]``) and saved under ``("tidal", user_id)``.

    Raises:
        DeviceCodeUnsupportedError: The endpoint 404s or rejects the
            request shape — the CLI's signal to fall back to the browser
            flow.
        DeviceCodeExpiredError: The grant expired before approval — the
            user must rerun the command.
    """
    async with make_tidal_auth_client() as client:
        response = await client.post(
            "/v1/oauth2/device_authorization",
            data={
                "client_id": settings.credentials.tidal_client_id,
                "scope": " ".join(TIDAL_SCOPES),
            },
        )
        if response.status_code == HTTPStatus.NOT_FOUND or (
            response.status_code == HTTPStatus.BAD_REQUEST
            and _oauth_error_code(response) in _UNSUPPORTED_ERROR_CODES
        ):
            logger.info(
                "Tidal device_authorization unavailable — signalling fallback",
                status_code=response.status_code,
            )
            raise DeviceCodeUnsupportedError
        _ = response.raise_for_status()
        grant = _parse_device_authorization(parse_json_response(response))
        if on_verification is not None:
            on_verification(grant)
        token = await _poll_device_token(client, grant, sleep=sleep)

    token["extra_data"] = {"authorized_at": int(time.time())}
    await storage.save_token("tidal", user_id, token)
    logger.info("Tidal device authorization complete — token obtained")
    return token


def _cli_redirect_uri() -> str:
    """The redirect the CLI browser fallback listens on.

    ``tidal_cli_redirect_uri`` when configured (the primary redirect is the
    web callback, whose origin no local listener can bind), falling back to
    the primary when unset. Both URIs must be registered in the Tidal
    dashboard.
    """
    return (
        settings.credentials.tidal_cli_redirect_uri
        or settings.credentials.tidal_redirect_uri
    )


def _redirect_port(redirect_uri: str) -> int:
    """Local listen port for the one-shot callback server, from the URI."""
    parsed = urllib.parse.urlparse(redirect_uri)
    if parsed.port is not None:
        return parsed.port
    if parsed.scheme == "http":
        return 80
    if parsed.scheme == "https":
        return 443
    raise RuntimeError(
        f"Cannot determine a local callback port from {redirect_uri!r} — "
        "set TIDAL_CLI_REDIRECT_URI to a loopback URI such as "
        "http://127.0.0.1:8899/callback (and register it in the Tidal "
        "dashboard alongside the web TIDAL_REDIRECT_URI)."
    )


def _capture_redirect(auth_url: str, port: int) -> dict[str, str]:
    """Open the browser and capture one OAuth redirect on 127.0.0.1:port.

    Mirrors Spotify's ``run_browser_auth`` server: a blocking one-shot
    ``HTTPServer`` that answers exactly one request (the callback) and
    returns the captured ``code`` + ``state``.
    """
    captured: dict[str, str] = {}

    class _CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(parsed.query)
            captured["code"] = qs.get("code", [""])[0]
            captured["state"] = qs.get("state", [""])[0]
            self.send_response(HTTPStatus.OK)
            self.end_headers()
            _ = self.wfile.write(
                b"Tidal authorization successful. You may close this tab."
            )

        @override
        def log_message(self, format: str, *args: object) -> None:
            pass  # Suppress HTTP server access logs

    server = HTTPServer(("127.0.0.1", port), _CallbackHandler)
    logger.info("Opening Tidal authorization in browser...")
    _ = webbrowser.open(auth_url)
    server.handle_request()  # Block until exactly one request (the callback)
    server.server_close()
    return captured


async def run_browser_auth(storage: TokenStorage, user_id: str) -> StoredToken:
    """Localhost-redirect fallback: browser authorize + in-process PKCE.

    The state and code verifier live in this process for the one attempt —
    no ``DBOAuthState`` round-trip, unlike the web callback — and the
    listen port comes from the CLI redirect (``tidal_cli_redirect_uri``,
    falling back to the primary ``tidal_redirect_uri``); that same URI
    rides the authorize URL and the token exchange, as OAuth requires.

    Raises:
        RuntimeError: No code captured, or the returned state doesn't match
            the one minted for this attempt (possible CSRF).
    """
    redirect_uri = _cli_redirect_uri()
    code_verifier = secrets.token_urlsafe(64)
    state = secrets.token_urlsafe(16)
    params = {
        "client_id": settings.credentials.tidal_client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": " ".join(TIDAL_SCOPES),
        "state": state,
        "code_challenge_method": "S256",
        "code_challenge": _compute_pkce_challenge(code_verifier),
    }
    auth_url = f"{TIDAL_AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"
    port = _redirect_port(redirect_uri)

    captured = await asyncio.to_thread(_capture_redirect, auth_url, port)

    if not captured.get("code"):
        raise RuntimeError(
            "Tidal authorization failed — no authorization code received. "
            "Ensure TIDAL_CLI_REDIRECT_URI (or TIDAL_REDIRECT_URI when it "
            "is unset) matches a loopback redirect registered on the "
            "Tidal app."
        )
    if captured.get("state") != state:
        raise RuntimeError(
            "Tidal authorization failed — OAuth state mismatch (possible CSRF)."
        )

    token = await exchange_code(
        captured["code"], code_verifier, redirect_uri=redirect_uri
    )
    await storage.save_token("tidal", user_id, token)
    logger.info("Tidal browser authorization complete — token obtained")
    return token
