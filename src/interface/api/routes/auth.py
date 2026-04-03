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

import base64
from datetime import UTC, datetime, timedelta
import hashlib
import secrets
import urllib.parse

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import delete

from src.config import get_logger, settings
from src.infrastructure.connectors._shared.connector_status import (
    _fetch_spotify_display_name,
)
from src.infrastructure.connectors._shared.token_storage import (
    StoredToken,
    get_token_storage,
)
from src.infrastructure.connectors.spotify.auth import (
    SPOTIFY_AUTHORIZE_URL,
    SPOTIFY_SCOPES,
    SpotifyTokenManager,
)
from src.interface.api.deps import get_current_user_id

logger = get_logger(__name__)

router = APIRouter(tags=["auth"])

# ---------------------------------------------------------------------------
# CSRF STATE MANAGEMENT (database-backed)
# ---------------------------------------------------------------------------

_CSRF_STATE_TTL = timedelta(minutes=5)


def _create_pkce_challenge(code_verifier: str) -> str:
    """Compute S256 PKCE code_challenge from a code_verifier (RFC 7636)."""

    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


async def _create_state(
    user_id: str,
    service: str,
    *,
    code_verifier: str | None = None,
) -> str:
    """Generate a CSRF state token and persist it to the database.

    Stores user_id alongside the state so the callback can identify
    which user initiated the OAuth flow. Also prunes expired rows.
    """
    from src.infrastructure.persistence.database.db_connection import get_session
    from src.infrastructure.persistence.database.db_models import DBOAuthState

    state = secrets.token_urlsafe(32)
    now = datetime.now(UTC)

    async with get_session() as session:
        # Prune expired states
        await session.execute(delete(DBOAuthState).where(DBOAuthState.expires_at < now))

        session.add(
            DBOAuthState(
                state=state,
                user_id=user_id,
                service=service,
                code_verifier=code_verifier,
                expires_at=now + _CSRF_STATE_TTL,
            )
        )

    return state


async def _validate_state(state: str) -> tuple[bool, str | None, str | None]:
    """Validate and consume a CSRF state token from the database.

    Returns (is_valid, code_verifier, user_id). Uses DELETE...RETURNING
    for atomic consume — single round-trip, no race window.
    """
    from src.infrastructure.persistence.database.db_connection import get_session
    from src.infrastructure.persistence.database.db_models import DBOAuthState

    if not state:
        return False, None, None

    now = datetime.now(UTC)

    async with get_session() as session:
        result = await session.execute(
            delete(DBOAuthState)
            .where(
                DBOAuthState.state == state,
                DBOAuthState.expires_at > now,
            )
            .returning(DBOAuthState.code_verifier, DBOAuthState.user_id)
        )
        row = result.one_or_none()
        if row is None:
            return False, None, None

    return True, row.code_verifier, row.user_id


# ---------------------------------------------------------------------------
# AUTH URL GENERATION (JSON API — called by frontend)
# ---------------------------------------------------------------------------


@router.get("/api/v1/connectors/spotify/auth-url")
async def get_spotify_auth_url(
    user_id: str = Depends(get_current_user_id),
) -> dict[str, str]:
    """Generate Spotify OAuth authorization URL with CSRF state and PKCE."""
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = _create_pkce_challenge(code_verifier)
    state = await _create_state(user_id, "spotify", code_verifier=code_verifier)
    params = {
        "client_id": settings.credentials.spotify_client_id,
        "response_type": "code",
        "redirect_uri": settings.credentials.spotify_redirect_uri,
        "scope": " ".join(SPOTIFY_SCOPES),
        "state": state,
        "show_dialog": "false",
        "code_challenge_method": "S256",
        "code_challenge": code_challenge,
    }
    auth_url = f"{SPOTIFY_AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"
    return {"auth_url": auth_url}


@router.get("/api/v1/connectors/lastfm/auth-url")
async def get_lastfm_auth_url(
    request: Request,
    user_id: str = Depends(get_current_user_id),
) -> dict[str, str]:
    """Generate Last.fm web auth URL.

    Derives the callback URL from the request's Host header so it works
    for both localhost development and production deployment. Embeds a
    ``_state`` query param in the callback URL since Last.fm has no native
    state parameter.
    """
    api_key = settings.credentials.lastfm_key
    state = await _create_state(user_id, "lastfm")

    # Derive callback URL from current request, including our state token
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.url.netloc)
    callback_url = f"{scheme}://{host}/auth/lastfm/callback?_state={state}"

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

    valid, code_verifier, user_id = await _validate_state(state)
    if not valid or not user_id:
        logger.warning("Spotify auth callback with invalid CSRF state")
        return RedirectResponse(
            "/settings/integrations?auth=spotify&status=error&reason=invalid_state"
        )

    try:
        storage = get_token_storage()
        mgr = SpotifyTokenManager(storage=storage, user_id=user_id)
        token_info = await mgr.exchange_code(code, code_verifier=code_verifier)

        # Fetch display name before saving to avoid a double upsert
        display_name = await _fetch_spotify_display_name(token_info["access_token"])
        token_to_save = StoredToken(**token_info)
        if display_name:
            token_to_save = StoredToken(**token_info, account_name=display_name)
        await storage.save_token("spotify", user_id, token_to_save)

        logger.info("Spotify web auth completed successfully", user_id=user_id)
        return RedirectResponse("/settings/integrations?auth=spotify&status=success")

    except Exception:
        logger.error("Spotify auth callback failed", exc_info=True)
        return RedirectResponse(
            "/settings/integrations?auth=spotify&status=error&reason=exchange_failed"
        )


@router.get("/auth/lastfm/callback")
async def lastfm_callback(token: str = "", _state: str = "") -> RedirectResponse:
    """Last.fm auth callback — exchanges token for permanent session key.

    Last.fm's web auth flow:
    1. User authorized on last.fm, redirected here with ?token=TOKEN&_state=STATE
    2. We validate the state to recover user_id
    3. We call auth.getSession to exchange the token for a permanent session key
    4. Session key stored in database, user redirected to /settings
    """
    if not token:
        logger.warning("Last.fm auth callback with no token")
        return RedirectResponse(
            "/settings/integrations?auth=lastfm&status=error&reason=no_token"
        )

    # Validate state to recover user_id
    valid, _, user_id = await _validate_state(_state)
    if not valid or not user_id:
        logger.warning("Last.fm auth callback with invalid state")
        return RedirectResponse(
            "/settings/integrations?auth=lastfm&status=error&reason=invalid_state"
        )

    api_key = settings.credentials.lastfm_key
    api_secret = settings.credentials.lastfm_secret.get_secret_value()

    if not api_key or not api_secret:
        logger.error("Last.fm API key/secret not configured")
        return RedirectResponse(
            "/settings/integrations?auth=lastfm&status=error&reason=not_configured"
        )

    try:
        from src.infrastructure.connectors.lastfm.client import LastFMAPIClient

        async with LastFMAPIClient() as lastfm_client:
            session_key, username = await lastfm_client.exchange_web_auth_token(token)

        # Store permanent session key
        storage = get_token_storage()
        await storage.save_token(
            "lastfm",
            user_id,
            StoredToken(
                session_key=session_key,
                token_type="session",  # noqa: S106 — metadata label, not a secret
                account_name=username,
            ),
        )

        logger.info(f"Last.fm web auth completed for user {username}", user_id=user_id)
        return RedirectResponse("/settings/integrations?auth=lastfm&status=success")

    except Exception:
        logger.error("Last.fm auth callback failed", exc_info=True)
        return RedirectResponse(
            "/settings/integrations?auth=lastfm&status=error&reason=exchange_failed"
        )
