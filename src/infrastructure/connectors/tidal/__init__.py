"""Tidal connector package (v0.11.3).

OAuth 2.1 + PKCE auth (``auth.py`` — browser flow, rotation-safe refresh
through the shared single-flight guard), the error classifier, settings
(``settings.api.tidal``, ``settings.credentials.tidal_client_id`` /
``tidal_redirect_uri``), and shared httpx2 client factories
(``make_tidal_client``, ``make_tidal_auth_client`` in
``_shared/http_client.py``).

Exports ``get_connector_config`` so connector discovery
(``src.infrastructure.connectors.discovery.discover_connectors``) registers
Tidal: ``TidalConnector`` is a minimal facade holding the JSON:API client
(T5). Conservative track resolution (T9, both ISRC-only):

- ``TidalMatchingProvider``: enrichment matching, registered with
  ``TrackIdentityServiceImpl``'s provider factories
- ``TidalInwardResolver``: track id → canonical minting with the
  ``replacement`` successor consult (the first ``SuccessorHook``
  implementor). No play channel exists for Tidal — the resolver's consumers
  are the v0.12.0 spike and the v0.13.4 library sync, not the play-import
  registry.
"""

from src.infrastructure.connectors.tidal.connector import (
    TidalConnector,
    get_connector_config,
)
from src.infrastructure.connectors.tidal.error_classifier import (
    TidalErrorClassifier,
)
from src.infrastructure.connectors.tidal.inward_resolver import TidalInwardResolver
from src.infrastructure.connectors.tidal.matching_provider import (
    TidalMatchingProvider,
)

__all__ = [
    "TidalConnector",
    "TidalErrorClassifier",
    "TidalInwardResolver",
    "TidalMatchingProvider",
    "get_connector_config",
]
