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
from http import HTTPStatus
import secrets
import time
from typing import TYPE_CHECKING, Final
import urllib.parse

from attrs import define, field

from src.config import get_logger, settings
from src.domain.entities.shared import JsonValue
from src.domain.exceptions import (
    TidalAuthRequiredError,
    TidalReauthRequiredError,
)
from src.infrastructure.connectors._shared.http_client import (
    TIDAL_LOGIN_BASE,
    make_tidal_auth_client,
    parse_json_response,
)
from src.infrastructure.connectors._shared.oauth import (
    BearerAuth,
    compute_pkce_challenge,
    is_invalid_grant,
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
    code_challenge = compute_pkce_challenge(code_verifier)
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


def stored_token_from_response(raw: dict[str, JsonValue]) -> StoredToken:
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

    token = stored_token_from_response(raw)
    token["extra_data"] = {"authorized_at": int(time.time())}
    logger.info("Tidal authorization complete — token obtained")
    return token


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
            if response.status_code == HTTPStatus.BAD_REQUEST and is_invalid_grant(
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

        new_token = stored_token_from_response(raw)
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


class TidalBearerAuth(BearerAuth):
    """Shared bearer-inject + one-401-retry flow over ``TidalTokenManager``."""
