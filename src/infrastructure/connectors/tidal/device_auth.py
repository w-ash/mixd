"""Tidal CLI auth flows (v0.11.3 T6): device code + browser fallback.

Device-code flow (RFC 8628) is the primary CLI path — but Tidal's
``POST /v1/oauth2/device_authorization`` endpoint is UNDOCUMENTED, so the
flow is built to fail *typed* (DeviceCodeUnsupportedError) when the
endpoint turns out not to exist, letting :func:`run_auth` auto-fall back
to the localhost-redirect browser flow. The T7 live probe decides which
survives.

The web callback's machinery (``build_auth_url``, ``exchange_code``, the
token manager) stays in ``tidal/auth.py``; this module holds only what the
CLI connect needs on top of it.
"""

import asyncio
import collections.abc
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
import secrets
import time
from typing import override
import urllib.parse
import webbrowser

from attrs import define
import httpx2

from src.config import get_logger, settings
from src.domain.entities.shared import JsonValue
from src.infrastructure.connectors._shared.http_client import (
    make_tidal_auth_client,
    parse_json_body,
    parse_json_response,
)
from src.infrastructure.connectors._shared.oauth import compute_pkce_challenge
from src.infrastructure.connectors._shared.token_storage import (
    StoredToken,
    TokenStorage,
)
from src.infrastructure.connectors.tidal.auth import (
    TIDAL_AUTHORIZE_URL,
    TIDAL_SCOPES,
    exchange_code,
    stored_token_from_response,
)

logger = get_logger(__name__).bind(service="tidal_auth")

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
            return stored_token_from_response(parse_json_response(response))
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
        "code_challenge": compute_pkce_challenge(code_verifier),
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


async def run_auth(
    storage: TokenStorage,
    user_id: str,
    *,
    prefer_browser: bool = False,
    on_verification: (
        collections.abc.Callable[[DeviceAuthorization], None] | None
    ) = None,
    on_fallback: collections.abc.Callable[[], None] | None = None,
) -> StoredToken:
    """Connect Tidal from the CLI, owning the device→browser fallback policy.

    Runs the device-code flow unless ``prefer_browser`` forces the
    localhost-redirect flow; only :class:`DeviceCodeUnsupportedError` (the
    endpoint doesn't speak device flow) triggers the automatic fallback —
    ``on_fallback`` is invoked first so the CLI can say why. Every other
    failure (expiry, denial, transport) surfaces unchanged.
    """
    if prefer_browser:
        return await run_browser_auth(storage, user_id)
    try:
        return await run_device_auth(storage, user_id, on_verification=on_verification)
    except DeviceCodeUnsupportedError:
        if on_fallback is not None:
            on_fallback()
        return await run_browser_auth(storage, user_id)
