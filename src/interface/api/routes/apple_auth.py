"""Apple Music in-app MusicKit connect routes.

Apple Music has no OAuth authorization-code flow: per-user access rides on a
Music User Token (MUT) that only MusicKit JS running in the user's browser
can mint. The SPA runs the whole flow on the app page itself (the
``browser_bridge`` auth method — browser-mediated, still not OAuth):

1. ``GET /api/v1/connectors/apple_music/musickit-config`` hands the SPA the
   developer token — a browser-facing credential by design; the ``.p8``
   signing key never leaves the server.
2. The SPA loads MusicKit JS, calls ``configure()`` + ``authorize()`` (Apple's
   own login sheet is the only popup the user sees), and
3. POSTs the resulting MUT to ``POST /api/v1/connectors/apple_music/token``,
   which stores it under the authenticated user and best-effort records the
   user's storefront.

Both routes bind ``user_id`` from ``get_current_user_id`` like every other
API route — the requests originate from the authenticated SPA, so there is
no CSRF-state carrier (the old server-rendered bridge page and its state
round-trip were removed with the in-app rebuild).

Kept separate from ``auth.py`` so the OAuth callback machinery stays clean;
both live in the credential/OAuth carve-out (interface-patterns.md).
"""

from datetime import UTC, datetime, timedelta
from typing import Final

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from src.config import get_logger
from src.infrastructure.connectors._shared.token_storage import (
    StoredToken,
    get_token_storage,
)
from src.infrastructure.connectors.apple_music.auth import (
    DeveloperTokenProvider,
    backfill_storefront,
)
from src.interface.api.deps import get_current_user_id

logger = get_logger(__name__)

router = APIRouter(tags=["auth"])

# Apple MUT fixed lifetime ~6 months, no refresh.
MUT_TTL: Final = timedelta(days=182)


class MusicKitConfigResponse(BaseModel):
    """MusicKit JS configuration for the SPA's in-app connect flow."""

    developer_token: str


class AppleMusicTokenRequest(BaseModel):
    """Body of the SPA's Music User Token POST."""

    music_user_token: str = Field(min_length=1)


@router.get("/api/v1/connectors/apple_music/musickit-config")
async def get_musickit_config(
    user_id: str = Depends(get_current_user_id),
) -> MusicKitConfigResponse:
    """Return the developer token the SPA feeds to ``MusicKit.configure()``.

    The developer token is browser-safe by design (it is embedded in every
    MusicKit page on the web); the signing key never leaves the server.
    """
    del user_id  # Authenticated route; the token itself is not per-user.
    try:
        developer_token = DeveloperTokenProvider().get_token()
    except RuntimeError as err:
        logger.error("Apple Music developer token unavailable", exc_info=True)
        raise HTTPException(
            status_code=503,
            detail=(
                "Apple Music is not configured on this server. "
                "Set APPLE_TEAM_ID, APPLE_KEY_ID, and APPLE_PRIVATE_KEY."
            ),
        ) from err
    return MusicKitConfigResponse(developer_token=developer_token)


@router.post("/api/v1/connectors/apple_music/token", status_code=204)
async def store_apple_music_token(
    body: AppleMusicTokenRequest,
    user_id: str = Depends(get_current_user_id),
) -> Response:
    """Persist the Music User Token under the authenticated user.

    The storefront lookup is best-effort: Apple being unreachable must not
    fail the connect.
    """
    now = int(datetime.now(UTC).timestamp())
    token: StoredToken = {
        "access_token": body.music_user_token,
        "token_type": "music_user_token",
        "expires_at": now + int(MUT_TTL.total_seconds()),
        "extra_data": {"authorized_at": now},
    }
    storage = get_token_storage()
    await storage.save_token("apple_music", user_id, token)

    await backfill_storefront(storage, user_id)

    logger.info("Apple Music web auth completed successfully", user_id=user_id)
    return Response(status_code=204)
