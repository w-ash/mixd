"""Apple Music connector module.

Connectable via the MusicKit JS browser bridge (``auth_method
"browser_bridge"``): the per-user Music User Token is minted in the user's
browser by ``interface/api/routes/apple_auth.py`` and stored in token
storage; the API client rides the locally minted developer token on every
request.

Components:
- AppleMusicAPIClient: Pure API wrapper (developer-token bearer on every
  request, per-user Music-User-Token on /v1/me/* calls)
- AppleMusicConnector: Minimal facade holding the client (Closeable)
- DeveloperTokenProvider: Mints/caches the ES256 developer token JWT
- AppleMusicDeveloperAuth: httpx2 auth flow injecting the developer token
- AppleMusicErrorClassifier: JSON:API-aware error classification
- AppleMusicMatchingProvider: Conservative ISRC-only track matching
- AppleMusicInwardResolver: Catalog ids → canonical tracks (ISRC-only)
- AppleMusicConnectorPlayResolver: connector_plays rows for service "apple"
- get_connector_config: Registry entry (``browser_bridge``)

Naming: the data-plane service name is ``"apple"`` (mappings, plays,
resolver registry); ``"apple_music"`` is the control-plane key (package,
settings, token storage, rate limiter).

Usage:
    from src.infrastructure.connectors.apple_music import AppleMusicAPIClient
    client = AppleMusicAPIClient()
    lookup = await client.get_songs_by_isrc("us", ["USUM72309818"])
"""

from src.infrastructure.connectors.apple_music.auth import (
    AppleMusicDeveloperAuth,
    DeveloperTokenProvider,
)
from src.infrastructure.connectors.apple_music.client import AppleMusicAPIClient
from src.infrastructure.connectors.apple_music.connector import (
    AppleMusicConnector,
    get_connector_config,
)
from src.infrastructure.connectors.apple_music.error_classifier import (
    AppleMusicErrorClassifier,
)
from src.infrastructure.connectors.apple_music.inward_resolver import (
    AppleMusicInwardResolver,
)
from src.infrastructure.connectors.apple_music.matching_provider import (
    AppleMusicMatchingProvider,
)
from src.infrastructure.connectors.apple_music.play_resolver import (
    AppleMusicConnectorPlayResolver,
)

__all__ = [
    "AppleMusicAPIClient",
    "AppleMusicConnector",
    "AppleMusicConnectorPlayResolver",
    "AppleMusicDeveloperAuth",
    "AppleMusicErrorClassifier",
    "AppleMusicInwardResolver",
    "AppleMusicMatchingProvider",
    "DeveloperTokenProvider",
    "get_connector_config",
]
