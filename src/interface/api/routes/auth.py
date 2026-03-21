"""OAuth callback routes for Spotify and Last.fm web authentication.

These routes handle browser redirects from external OAuth providers.
They are mounted at the top level (not under /api/v1) because they are
redirect targets for external services, not JSON API endpoints.

Flow:
1. Frontend calls GET /api/v1/connectors/{service}/auth-url (in connectors.py)
2. User is redirected to the external service for authorization
3. Service redirects back to /auth/{service}/callback (this file)
4. Callback exchanges code/token, stores credentials, redirects to /settings/integrations
"""

# pyright: reportAny=false, reportExplicitAny=false

import secrets
import time
import urllib.parse

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
import httpx

from src.config import get_logger, settings
from src.infrastructure.connectors._shared.connector_status import (
    _fetch_spotify_display_name,
)
from src.infrastructure.connectors._shared.token_storage import (
    StoredToken,
    get_token_storage,
)
from src.infrastructure.connectors.lastfm.client import _sign_params
from src.infrastructure.connectors.spotify.auth import (
    SPOTIFY_AUTHORIZE_URL,
    SPOTIFY_SCOPES,
    SpotifyTokenManager,
)

logger = get_logger(__name__)

router = APIRouter(tags=["auth"])

# ---------------------------------------------------------------------------
# CSRF STATE MANAGEMENT
# ---------------------------------------------------------------------------

# In-memory state store with TTL. Single-user, single-worker app — no need
# for database or Redis. States expire after 5 minutes.
_CSRF_STATE_TTL = 300
_csrf_states: dict[str, float] = {}


def _create_state() -> str:
    """Generate a CSRF state token and store it with a TTL."""
    # Prune expired states
    now = time.time()
    expired = [k for k, v in _csrf_states.items() if v < now]
    for k in expired:
        del _csrf_states[k]

    state = secrets.token_urlsafe(32)
    _csrf_states[state] = now + _CSRF_STATE_TTL
    return state


def _validate_state(state: str) -> bool:
    """Validate and consume a CSRF state token."""
    expiry = _csrf_states.pop(state, None)
    if expiry is None:
        return False
    return time.time() < expiry


# ---------------------------------------------------------------------------
# AUTH URL GENERATION (JSON API — called by frontend)
# ---------------------------------------------------------------------------


@router.get("/api/v1/connectors/spotify/auth-url")
async def get_spotify_auth_url() -> dict[str, str]:
    """Generate Spotify OAuth authorization URL with CSRF state."""
    state = _create_state()
    params = {
        "client_id": settings.credentials.spotify_client_id,
        "response_type": "code",
        "redirect_uri": settings.credentials.spotify_redirect_uri,
        "scope": " ".join(SPOTIFY_SCOPES),
        "state": state,
        "show_dialog": "false",
    }
    auth_url = f"{SPOTIFY_AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"
    return {"auth_url": auth_url}


@router.get("/api/v1/connectors/lastfm/auth-url")
async def get_lastfm_auth_url(request: Request) -> dict[str, str]:
    """Generate Last.fm web auth URL.

    Derives the callback URL from the request's Host header so it works
    for both localhost development and production deployment.
    """
    api_key = settings.credentials.lastfm_key

    # Derive callback URL from current request
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.url.netloc)
    callback_url = f"{scheme}://{host}/auth/lastfm/callback"

    params = {
        "api_key": api_key,
        "cb": callback_url,
    }
    auth_url = f"https://www.last.fm/api/auth/?{urllib.parse.urlencode(params)}"
    return {"auth_url": auth_url}


# ---------------------------------------------------------------------------
# OAUTH CALLBACKS (browser redirects)
# ---------------------------------------------------------------------------


@router.get("/auth/spotify/callback")
async def spotify_callback(
    code: str = "", state: str = "", error: str = ""
) -> RedirectResponse:
    """Spotify OAuth callback — exchanges code for tokens, stores them.

    On success, redirects to /settings/integrations?auth=spotify&status=success.
    On failure, redirects to /settings/integrations?auth=spotify&status=error.
    """
    if error or not code:
        logger.warning(f"Spotify auth denied or failed: {error}")
        return RedirectResponse(
            f"/settings/integrations?auth=spotify&status=error&reason={urllib.parse.quote(error)}"
        )

    if not _validate_state(state):
        logger.warning("Spotify auth callback with invalid CSRF state")
        return RedirectResponse(
            "/settings/integrations?auth=spotify&status=error&reason=invalid_state"
        )

    try:
        storage = get_token_storage()
        mgr = SpotifyTokenManager(storage=storage)
        token_info = await mgr.exchange_code(code)
        await storage.save_token("spotify", StoredToken(**token_info))

        # Fetch display name and cache it with the token
        display_name = await _fetch_spotify_display_name(token_info["access_token"])
        if display_name:
            token_with_name = StoredToken(**token_info, account_name=display_name)
            await storage.save_token("spotify", token_with_name)

        logger.info("Spotify web auth completed successfully")
        return RedirectResponse("/settings/integrations?auth=spotify&status=success")

    except Exception:
        logger.opt(exception=True).error("Spotify auth callback failed")
        return RedirectResponse(
            "/settings/integrations?auth=spotify&status=error&reason=exchange_failed"
        )


@router.get("/auth/lastfm/callback")
async def lastfm_callback(token: str = "") -> RedirectResponse:
    """Last.fm auth callback — exchanges token for permanent session key.

    Last.fm's web auth flow:
    1. User authorized on last.fm, redirected here with ?token=TOKEN
    2. We call auth.getSession to exchange the token for a permanent session key
    3. Session key stored in database, user redirected to /settings
    """
    if not token:
        logger.warning("Last.fm auth callback with no token")
        return RedirectResponse(
            "/settings/integrations?auth=lastfm&status=error&reason=no_token"
        )

    api_key = settings.credentials.lastfm_key
    api_secret = settings.credentials.lastfm_secret.get_secret_value()

    if not api_key or not api_secret:
        logger.error("Last.fm API key/secret not configured")
        return RedirectResponse(
            "/settings/integrations?auth=lastfm&status=error&reason=not_configured"
        )

    try:
        # Exchange token for permanent session key via auth.getSession
        sig_params = {
            "method": "auth.getSession",
            "api_key": api_key,
            "token": token,
        }
        api_sig = _sign_params(sig_params, api_secret)

        request_params = {
            **sig_params,
            "api_sig": api_sig,
            "format": "json",
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://ws.audioscrobbler.com/2.0/",
                params=request_params,
            )
            resp.raise_for_status()
            data: dict[str, object] = resp.json()

        if "error" in data:
            error_msg = data.get("message", "Unknown error")
            logger.warning(f"Last.fm auth.getSession failed: {error_msg}")
            return RedirectResponse(
                f"/settings/integrations?auth=lastfm&status=error&reason={urllib.parse.quote(str(error_msg))}"
            )

        raw_session = data.get("session")
        if not isinstance(raw_session, dict):
            logger.error("Last.fm auth.getSession returned no session object")
            return RedirectResponse(
                "/settings/integrations?auth=lastfm&status=error&reason=no_session"
            )
        session_key = str(raw_session.get("key", ""))
        username = str(raw_session.get("name", ""))

        if not session_key:
            logger.error("Last.fm auth.getSession returned no session key")
            return RedirectResponse(
                "/settings/integrations?auth=lastfm&status=error&reason=no_session_key"
            )

        # Store permanent session key
        storage = get_token_storage()
        await storage.save_token(
            "lastfm",
            StoredToken(
                session_key=str(session_key),
                token_type="session",
                account_name=str(username),
            ),
        )

        logger.info(f"Last.fm web auth completed for user {username}")
        return RedirectResponse("/settings/integrations?auth=lastfm&status=success")

    except Exception:
        logger.opt(exception=True).error("Last.fm auth callback failed")
        return RedirectResponse(
            "/settings/integrations?auth=lastfm&status=error&reason=exchange_failed"
        )
