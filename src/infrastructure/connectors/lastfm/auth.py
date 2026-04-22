"""Last.fm web-auth URL builder.

Last.fm's web-auth flow does not use RFC 6749 OAuth; instead the caller
redirects the user to ``https://www.last.fm/api/auth/`` with a ``cb``
parameter pointing at our callback URL. Because Last.fm does not echo an
arbitrary ``state`` parameter back, we embed our CSRF/session state
directly in the callback URL as ``?_state=…``.

This module only owns the *URL assembly*. CSRF state storage lives in
``src.interface.api.routes.auth._create_state`` and is injected as a
callable so the security-critical DB layer stays centralized.
"""

from typing import TYPE_CHECKING
import urllib.parse

from src.config import settings

if TYPE_CHECKING:
    from fastapi import Request

    from src.infrastructure.connectors.protocols import CreateStateFn

LASTFM_AUTH_URL = "https://www.last.fm/api/auth/"


async def build_auth_url(
    user_id: str,
    request: Request,
    create_state: CreateStateFn,
) -> str:
    """Assemble Last.fm's web-auth URL with CSRF state embedded in the callback."""
    api_key = settings.credentials.lastfm_key
    state = await create_state(user_id, "lastfm")

    # Derive callback URL from current request so this works in both localhost
    # development and production deployments (where a reverse proxy adds the
    # x-forwarded-* headers).
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.url.netloc)
    callback_url = f"{scheme}://{host}/auth/lastfm/callback?_state={state}"

    params = {
        "api_key": api_key,
        "cb": callback_url,
    }
    return f"{LASTFM_AUTH_URL}?{urllib.parse.urlencode(params)}"
