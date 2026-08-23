"""Discogs connector package (v0.11.1 foundations).

Auth strategy, Pydantic models, API client, instance-wide pacer, error
classifier, connect-time token validation, and the registry entry for the
Discogs API. ``get_connector_config`` is re-exported here so connector
discovery registers the package (BYO personal access token, ``physical``
category).

Usage:
    from src.infrastructure.connectors.discogs import DiscogsAPIClient
    client = DiscogsAPIClient()
    identity = await client.get_identity()
"""

from src.infrastructure.connectors.discogs.client import DiscogsAPIClient
from src.infrastructure.connectors.discogs.connector import (
    DiscogsConnector,
    get_connector_config,
)

__all__ = [
    "DiscogsAPIClient",
    "DiscogsConnector",
    "get_connector_config",
]
