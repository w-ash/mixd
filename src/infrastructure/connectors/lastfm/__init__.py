"""Last.fm connector module.

Modular Last.fm API integration with clear separation of concerns.

Components:
- LastFMAPIClient: Pure API wrapper with authentication and rate limiting
- LastFMOperations: Business logic for complex workflows
- LastFMTrackInfo: Data model for Last.fm track information
- LastFMConnector: Main facade implementing connector protocols
- LastFMProvider: Track matching provider for identity resolution

Usage:
    from src.infrastructure.connectors.lastfm import LastFMConnector
    connector = LastFMConnector()
    track_info = await connector.get_track_info(artist, title)
"""

from src.infrastructure.connectors.lastfm.connector import (
    LastFMConnector,
    LastFmMetricResolver,
    get_connector_config,
)
from src.infrastructure.connectors.lastfm.conversions import LastFMTrackInfo
from src.infrastructure.connectors.lastfm.matching_provider import LastFMProvider

__all__ = [
    "LastFMConnector",
    "LastFMProvider",
    "LastFMTrackInfo",
    "LastFmMetricResolver",
    "get_connector_config",
]
