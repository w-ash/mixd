"""Shared OAuth building blocks for connector auth modules.

The provider-agnostic pieces Spotify and Tidal both need: S256 PKCE
challenge computation (RFC 7636), the ``invalid_grant`` refresh-rejection
probe, the dead-grant compare-and-delete, the refresh carry-forward merge,
the buffered expiry check, and the bearer-injection httpx2 auth flow with
its one-forced-refresh-then-replay 401 recovery. ``build_auth_url`` stays
per-connector — the authorize-request param dicts differ enough that a
shared assembler would hide what each provider actually sends.
"""

import base64
import collections.abc
import hashlib
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
import time
from typing import Protocol, override
import urllib.parse
import webbrowser

import httpx2

from src.config import get_logger
from src.infrastructure.connectors._shared.http_client import parse_json_body
from src.infrastructure.connectors._shared.token_storage import (
    StoredToken,
    TokenStorage,
)

logger = get_logger(__name__).bind(service="connector_oauth")


def compute_pkce_challenge(code_verifier: str) -> str:
    """Compute S256 PKCE code_challenge from a code_verifier (RFC 7636)."""
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def is_invalid_grant(response: httpx2.Response) -> bool:
    """True if a refresh-POST body carries ``error: "invalid_grant"``.

    A body that doesn't parse as a JSON object reads as "not invalid_grant"
    — it falls through to the plain HTTPStatusError path rather than raising.
    """
    body = parse_json_body(response)
    return body is not None and body.get("error") == "invalid_grant"


def token_expired(token: StoredToken, buffer_seconds: int = 300) -> bool:
    """True if the token expires within ``buffer_seconds`` (default 300).

    The buffer gives headroom against provider clock skew or early
    invalidation — a token near expiry refreshes proactively instead of
    being spent on a request that would 401. A token without ``expires_at``
    reads as expired.
    """
    return int(time.time()) > token.get("expires_at", 0) - buffer_seconds


async def delete_grant_if_unchanged(
    storage: TokenStorage, service: str, user_id: str, refresh_token: str
) -> None:
    """Compare-and-delete the stored token after an ``invalid_grant`` rejection.

    The dead grant can never succeed again, but a stale manager (long-lived
    worker, or a race with the connect flow) may hold a refresh token that
    was already superseded. Only delete the stored row if it still carries
    the refresh token that just failed — deleting on a mismatch would
    destroy a NEWER, working grant.
    """
    stored = await storage.load_token(service, user_id)
    if stored is not None and stored.get("refresh_token") == refresh_token:
        await storage.delete_token(service, user_id)
    else:
        logger.info(
            "Skipping dead-token deletion — stored refresh token "
            "differs from the one that failed (a newer grant exists)"
        )


def carry_forward_token_fields(
    new: StoredToken, previous: StoredToken | None
) -> StoredToken:
    """Merge refresh-omitted and mixd-owned fields from ``previous`` into ``new``.

    Providers may omit ``refresh_token`` and ``scope`` from a refresh
    response even though the grant is intact — losing ``scope`` reads
    downstream as "the grant covers nothing", and losing ``refresh_token``
    stores a pair with no way to renew it. ``extra_data`` and
    ``account_name`` are mixd's own fields (``authorized_at``, cached
    counts, the connector card's display name), never the provider's —
    they carry forward verbatim. Values present in ``new`` win, except
    ``extra_data``/``account_name``, which the provider never sends.
    Mutates and returns ``new``.
    """
    if previous is None:
        return new
    if "refresh_token" not in new and (
        previous_refresh := previous.get("refresh_token")
    ):
        new["refresh_token"] = previous_refresh
    if "scope" not in new and (previous_scope := previous.get("scope")):
        new["scope"] = previous_scope
    if (previous_extra := previous.get("extra_data")) is not None:
        new["extra_data"] = previous_extra
    if (previous_name := previous.get("account_name")) is not None:
        new["account_name"] = previous_name
    return new


class RefreshableTokenManager(Protocol):
    """What :class:`BearerAuth` needs from a connector's token manager."""

    async def get_valid_token(self) -> str:
        """Return a valid access token, refreshing proactively if needed."""
        ...

    async def force_refresh(self) -> str:
        """Force-refresh after a 401, returning the fresh access token."""
        ...


class BearerAuth(httpx2.Auth):
    """httpx2 async auth flow: injects Bearer token and retries once on 401.

    Used with a long-lived AsyncClient so token injection and the
    one-forced-refresh-then-replay are handled transparently without
    per-call boilerplate. Connectors subclass this with their own token
    manager type (``SpotifyBearerAuth``, ``TidalBearerAuth``).
    """

    _token_manager: RefreshableTokenManager

    def __init__(self, token_manager: RefreshableTokenManager) -> None:
        self._token_manager = token_manager

    @override
    async def async_auth_flow(
        self, request: httpx2.Request
    ) -> collections.abc.AsyncGenerator[httpx2.Request, httpx2.Response]:
        token = await self._token_manager.get_valid_token()
        request.headers["Authorization"] = f"Bearer {token}"
        response = yield request

        if response.status_code == HTTPStatus.UNAUTHORIZED:
            new_token = await self._token_manager.force_refresh()
            request.headers["Authorization"] = f"Bearer {new_token}"
            yield request


def capture_loopback_redirect(
    auth_url: str, port: int, *, service_label: str
) -> dict[str, str]:
    """Open the browser and capture one OAuth redirect on 127.0.0.1:port.

    A blocking one-shot ``HTTPServer`` that answers exactly one request (the
    callback) and returns the captured ``code`` and ``state``. The state check
    belongs to the caller, which knows what it sent.
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
                f"{service_label} authorization successful. "
                "You may close this tab.".encode()
            )

        @override
        def log_message(self, format: str, *args: object) -> None:
            pass  # Suppress HTTP server access logs

    server = HTTPServer(("127.0.0.1", port), _CallbackHandler)
    logger.info(f"Opening {service_label} authorization in browser...")
    _ = webbrowser.open(auth_url)
    server.handle_request()  # Block until exactly one request (the callback)
    server.server_close()
    return captured
