"""Last.fm connector module.

Modular Last.fm API integration with clear separation of concerns.

Components:
- LastFMAPIClient: Pure API wrapper with authentication and rate limiting
- LastFMOperations: Business logic for complex workflows
- LastFMTrackInfo: Data model for Last.fm track information
- LastFMConnector: Main facade implementing connector protocols

Usage:
    from src.infrastructure.connectors.lastfm import LastFMConnector
    connector = LastFMConnector()
    track_info = await connector.get_track_info(artist, title)
"""

# Register LastFM metrics dynamically
from src.infrastructure.connectors._shared.metrics import register_connector_metrics
from src.infrastructure.connectors.lastfm.connector import (
    LastFMConnector,
    LastFmMetricResolver,
    get_connector_config,
)
from src.infrastructure.connectors.lastfm.conversions import LastFMTrackInfo

register_connector_metrics("lastfm", {
    "lastfm_user_playcount": {
        "field_name": "userplaycount",
        "freshness_hours": 1.0
    },
    "lastfm_global_playcount": {
        "field_name": "playcount", 
        "freshness_hours": 24.0
    },
    "lastfm_listeners": {
        "field_name": "listeners",
        "freshness_hours": 24.0
    }
})

__all__ = [
    "LastFMConnector",
    "LastFMTrackInfo",
    "LastFmMetricResolver", 
    "get_connector_config",
]