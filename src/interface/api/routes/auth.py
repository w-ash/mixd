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

from datetime import UTC, datetime, timedelta
import secrets
from typing import cast
import urllib.parse

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import delete

from src.config import get_logger, settings
from src.infrastructure.connectors._shared.connector_status import (
    fetch_spotify_display_name,
)
from src.infrastructure.connectors._shared.token_storage import (
    StoredToken,
    get_token_storage,
)
from src.infrastructure.connectors.discovery import discover_connectors
from src.infrastructure.connectors.spotify.auth import SpotifyTokenManager
from src.interface.api.deps import get_current_user_id

logger = get_logger(__name__)

router = APIRouter(tags=["auth"])

# ---------------------------------------------------------------------------
# CSRF STATE MANAGEMENT (database-backed)
# ---------------------------------------------------------------------------

_CSRF_STATE_TTL = timedelta(minutes=5)


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

    code_verifier = cast("str | None", row.code_verifier)
    user_id = cast("str | None", row.user_id)
    return True, code_verifier, user_id


# ---------------------------------------------------------------------------
# AUTH URL GENERATION (JSON API — called by frontend)
# ---------------------------------------------------------------------------


@router.get("/api/v1/connectors/{service}/auth-url")
async def get_connector_auth_url(
    service: str,
    request: Request,
    user_id: str = Depends(get_current_user_id),
) -> dict[str, str]:
    """Generate the OAuth authorization URL for the named connector.

    Dispatches via the connector registry: each OAuth-capable connector
    registers a ``build_auth_url`` callable (e.g. ``spotify/auth.py``,
    ``lastfm/auth.py``) that assembles the provider-specific URL. The
    CSRF + PKCE state factory is injected so security-sensitive DB state
    creation stays centralized in this file.

    Returns 404 for unknown services, 400 for non-OAuth connectors
    (``auth_method`` in ``{"none", "coming_soon"}``).
    """
    config = discover_connectors().get(service)
    if config is None:
        raise HTTPException(status_code=404, detail=f"Unknown connector: {service}")
    build = config["build_auth_url"]
    if config["auth_method"] != "oauth" or build is None:
        raise HTTPException(
            status_code=400, detail=f"{service} does not support OAuth authorization"
        )
    auth_url = await build(user_id, request, _create_state)
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
        display_name = await fetch_spotify_display_name(token_info["access_token"])
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
