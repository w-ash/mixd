"""Apple Music MusicKit JS bridge routes.

Apple Music has no OAuth authorization-code flow: per-user access rides on a
Music User Token (MUT) that only MusicKit JS running in the user's browser
can mint. These routes implement the ``browser_bridge`` connect path:

1. Frontend calls ``GET /api/v1/connectors/apple_music/auth-url`` (in
   ``auth.py``), which creates a CSRF state and returns the bridge URL.
2. The browser opens ``GET /auth/apple/authorize?state=...`` (this file) — a
   self-contained HTML page that loads MusicKit JS from Apple's CDN,
   configures it with the locally minted developer token, and runs
   ``music.authorize()``.
3. The page POSTs the resulting MUT + state to
   ``POST /api/v1/connectors/apple_music/token`` (this file), which consumes
   the state, stores the MUT, best-effort records the user's storefront, and
   the page redirects back to /settings/integrations.

Kept separate from ``auth.py`` so the OAuth callback machinery stays clean;
both live in the credential/OAuth carve-out (interface-patterns.md). The
developer token is embedded in the page by design — it is a browser-facing
credential; the ``.p8`` signing key never leaves the server.
"""

from datetime import UTC, datetime, timedelta
import json
from typing import Final

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field

from src import __version__
from src.config import get_logger
from src.infrastructure.connectors._shared.token_storage import (
    StoredToken,
    get_token_storage,
)
from src.infrastructure.connectors.apple_music.auth import DeveloperTokenProvider
from src.interface.api.routes.auth import validate_state

logger = get_logger(__name__)

router = APIRouter(tags=["auth"])

# Apple MUT fixed lifetime ~6 months, no refresh.
MUT_TTL: Final = timedelta(days=182)

MUSICKIT_JS_URL: Final = "https://js-cdn.music.apple.com/musickit/v3/musickit.js"

_RESULT_URL: Final = "/settings/integrations?auth=apple_music&status="

# Stricter referrer policies are a documented cause of MusicKit authorize()
# 403s — pin the exact value on the bridge page response.
_REFERRER_POLICY: Final = "strict-origin-when-cross-origin"


def _js_str(value: str) -> str:
    """Embed a string safely inside an inline <script> block.

    ``json.dumps`` handles quoting/escaping; escaping ``<`` additionally
    prevents ``</script>`` breakout from attacker-influenced values (the
    ``state`` query parameter).
    """
    return json.dumps(value).replace("<", "\\u003c")


def _page(developer_token: str, state: str) -> str:
    """The self-contained MusicKit JS bridge page."""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Connect Apple Music — Mixd</title>
<style>
  body {{ font-family: system-ui, sans-serif; background: #171412; color: #e8e4df;
         display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }}
  p {{ font-size: 1rem; opacity: 0.85; }}
</style>
</head>
<body>
<p>Connecting Apple Music&hellip; a sign-in window should appear.</p>
<script src="{MUSICKIT_JS_URL}" async></script>
<script>
const connectAppleMusic = async () => {{
  try {{
    await MusicKit.configure({{
      developerToken: {_js_str(developer_token)},
      app: {{ name: "Mixd", build: {_js_str(__version__)} }},
    }});
    const music = MusicKit.getInstance();
    const musicUserToken = await music.authorize();
    const resp = await fetch("/api/v1/connectors/apple_music/token", {{
      method: "POST",
      headers: {{ "Content-Type": "application/json" }},
      body: JSON.stringify({{ music_user_token: musicUserToken, state: {_js_str(state)} }}),
    }});
    if (!resp.ok) throw new Error("Token endpoint returned " + resp.status);
    window.location = {_js_str(_RESULT_URL + "success")};
  }} catch (err) {{
    console.error("Apple Music authorization failed", err);
    window.location = {_js_str(_RESULT_URL + "error&reason=authorize_failed")};
  }}
}};
// musickitloaded can fire before this inline script runs (cached MusicKit
// script evaluating first) — check synchronously, subscribe otherwise.
if (window.MusicKit) {{
  connectAppleMusic();
}} else {{
  document.addEventListener("musickitloaded", connectAppleMusic);
}}
</script>
</body>
</html>"""


@router.get("/auth/apple/authorize", response_class=HTMLResponse)
async def apple_authorize(state: str = "") -> HTMLResponse:
    """Serve the MusicKit JS bridge page for the given CSRF state.

    The state is minted by the auth-url route and merely carried through to
    the token POST, which validates and consumes it — this page holds no
    session and identifies no user.
    """
    try:
        developer_token = DeveloperTokenProvider().get_token()
    except RuntimeError:
        logger.error("Apple Music developer token unavailable", exc_info=True)
        return HTMLResponse(
            "<!doctype html><p>Apple Music is not configured on this server. "
            "Set APPLE_TEAM_ID, APPLE_KEY_ID, and APPLE_PRIVATE_KEY.</p>",
            status_code=503,
            headers={"Referrer-Policy": _REFERRER_POLICY},
        )
    return HTMLResponse(
        _page(developer_token, state),
        headers={"Referrer-Policy": _REFERRER_POLICY},
    )


class AppleMusicTokenRequest(BaseModel):
    """Body of the bridge page's token POST."""

    music_user_token: str = Field(min_length=1)
    state: str = Field(min_length=1)


@router.post("/api/v1/connectors/apple_music/token", status_code=204)
async def store_apple_music_token(body: AppleMusicTokenRequest) -> Response:
    """Validate the CSRF state and persist the Music User Token.

    The user is derived from the state row (created by the authenticated
    auth-url request), not from the ambient session — same trust model as the
    OAuth callbacks in ``auth.py``. The storefront lookup is best-effort:
    Apple being unreachable must not fail the connect.
    """
    valid, _, user_id = await validate_state(body.state, "apple_music")
    if not valid or not user_id:
        logger.warning("Apple Music token POST with invalid CSRF state")
        raise HTTPException(
            status_code=400, detail="Invalid or expired authorization state"
        )

    now = int(datetime.now(UTC).timestamp())
    extra_data: dict[str, object] = {"authorized_at": now}
    token: StoredToken = {
        "access_token": body.music_user_token,
        "token_type": "music_user_token",
        "expires_at": now + int(MUT_TTL.total_seconds()),
        "extra_data": extra_data,
    }
    storage = get_token_storage()
    await storage.save_token("apple_music", user_id, token)

    storefront = await _fetch_storefront(user_id)
    if storefront is not None:
        extra_data["storefront"] = storefront
        await storage.save_token("apple_music", user_id, token)

    logger.info("Apple Music web auth completed successfully", user_id=user_id)
    return Response(status_code=204)


async def _fetch_storefront(user_id: str) -> str | None:
    """Best-effort storefront id lookup with the freshly stored MUT.

    Any failure — Apple down, credentials unconfigured, token rejected —
    logs and returns None; it must never fail the connect.
    """
    try:
        return await _fetch_storefront_impl(user_id)
    except Exception:
        logger.warning(
            "Apple Music storefront fetch failed after connect", exc_info=True
        )
        return None


async def _fetch_storefront_impl(user_id: str) -> str | None:
    """Fetch the storefront id via the API client, closing its pool after.

    The client is constructed under ``user_context`` so it binds to the
    state row's user (it reads the MUT back from token storage).
    """
    from src.infrastructure.connectors.apple_music.client import AppleMusicAPIClient
    from src.infrastructure.persistence.database.user_context import user_context

    with user_context(user_id):
        client = AppleMusicAPIClient()
    try:
        storefront = await client.get_storefront()
    finally:
        await client.aclose()
    return storefront.id if storefront else None
