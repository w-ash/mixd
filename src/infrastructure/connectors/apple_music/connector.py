"""Apple Music connector facade and registry entry.

Apple Music connects in-app via MusicKit JS (``browser_bridge`` — browser-
mediated, not OAuth): the SPA fetches the developer token from
``GET /api/v1/connectors/apple_music/musickit-config``, runs
``music.authorize()`` on the app page itself, and the resulting Music User
Token is stored by ``interface/api/routes/apple_auth.py``. There is no
redirect URL to build, so ``build_auth_url`` is ``None``.

``AppleMusicConnector`` is deliberately minimal — it holds the API client and
satisfies the registry's ``Closeable`` cleanup contract. Playlist/track
operations (and a ``BaseAPIConnector`` facade) land with the first importer
that consumes them; declaring them now would be interface without behavior.
"""

from attrs import define, field

from src.infrastructure.connectors.apple_music.client import AppleMusicAPIClient
from src.infrastructure.connectors.protocols import ConnectorConfig


@define(slots=True)
class AppleMusicConnector:
    """Minimal Apple Music connector holding the API client.

    Exposes the client for direct use and implements ``aclose()`` so the
    registry / UoW cleanup path can release the pooled httpx2 client.
    """

    _client: AppleMusicAPIClient = field(
        init=False, factory=AppleMusicAPIClient, repr=False
    )

    @property
    def client(self) -> AppleMusicAPIClient:
        """Access to the underlying Apple Music API client."""
        return self._client

    async def aclose(self) -> None:
        """Close the underlying API client's HTTP connection pool."""
        await self._client.aclose()


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
        # In-app MusicKit connect — no redirect URL exists for this method.
        "build_auth_url": None,
    }
