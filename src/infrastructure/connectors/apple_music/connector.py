"""Apple Music connector facade and registry entry.

Apple Music connects via the MusicKit JS browser bridge (``browser_bridge``),
not OAuth: ``build_auth_url`` returns the in-app bridge page URL with a fresh
CSRF state, the page runs ``music.authorize()`` in the user's browser, and
the resulting Music User Token is stored by
``interface/api/routes/apple_auth.py``.

``AppleMusicConnector`` is deliberately minimal — it holds the API client and
satisfies the registry's ``Closeable`` cleanup contract. Playlist/track
operations (and a ``BaseAPIConnector`` facade) land with the first importer
that consumes them; declaring them now would be interface without behavior.
"""

from typing import TYPE_CHECKING
import urllib.parse

from attrs import define, field

from src.infrastructure.connectors.apple_music.client import AppleMusicAPIClient
from src.infrastructure.connectors.protocols import ConnectorConfig, CreateStateFn

if TYPE_CHECKING:
    # fastapi (~160ms import) is used only in annotations here; the guard
    # keeps it out of the CLI's connector import path.
    from fastapi import Request


@define(slots=True)
class AppleMusicConnector:
    """Minimal Apple Music connector holding the API client.

    Exposes the client for direct use and implements ``aclose()`` so the
    registry / UoW cleanup path can release the pooled httpx2 client.
    """

    _client: AppleMusicAPIClient = field(init=False, repr=False)

    def __attrs_post_init__(self) -> None:
        self._client = AppleMusicAPIClient()

    @property
    def client(self) -> AppleMusicAPIClient:
        """Access to the underlying Apple Music API client."""
        return self._client

    async def aclose(self) -> None:
        """Close the underlying API client's HTTP connection pool."""
        await self._client.aclose()


async def build_auth_url(
    user_id: str,
    request: Request,
    create_state: CreateStateFn,
) -> str:
    """Assemble the MusicKit JS bridge URL with a fresh CSRF state.

    Called from the generic ``/api/v1/connectors/{service}/auth-url`` route
    via the connector registry, exactly like ``spotify/auth.py`` — except the
    URL is our own bridge page, not an external provider, and there is no
    PKCE (MusicKit authorization is not OAuth; the state alone ties the
    browser round-trip back to the initiating user).
    """
    del request  # The bridge URL doesn't depend on the incoming request
    state = await create_state(user_id, "apple_music")
    return f"/auth/apple/authorize?{urllib.parse.urlencode({'state': state})}"


def get_connector_config() -> ConnectorConfig:
    """Apple Music connector configuration."""
    from src.infrastructure.connectors._shared.connector_status import (
        get_apple_music_status,
    )

    return {
        "dependencies": [],
        "factory": lambda _params: AppleMusicConnector(),
        # No register_metrics yet — enrichment is out of scope this cycle.
        "metrics": {},
        "display_name": "Apple Music",
        "category": "streaming",
        "auth_method": "browser_bridge",
        "capabilities": frozenset({"history_import_api"}),
        "status_fn": get_apple_music_status,
        "build_auth_url": build_auth_url,
    }
