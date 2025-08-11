"""Spotify connector module.

Modular Spotify API integration with clear separation of concerns.

Components:
- SpotifyAPIClient: Pure API wrapper with OAuth authentication
- SpotifyOperations: Business logic for complex workflows
- SpotifyConversions: Data transformation utilities  
- SpotifyConnector: Main facade implementing connector protocols

Usage:
    from src.infrastructure.connectors.spotify import SpotifyConnector
    connector = SpotifyConnector()
    await connector.create_playlist(...)
"""

# Register Spotify metrics dynamically
from src.infrastructure.connectors._shared.metrics import register_connector_metrics
from src.infrastructure.connectors.spotify.connector import (
    SpotifyConnector,
    get_connector_config,
)
from src.infrastructure.connectors.spotify.conversions import (
    convert_spotify_playlist_to_connector,
    convert_spotify_track_to_connector,
    extract_spotify_track_uris,
)

register_connector_metrics("spotify", {
    "spotify_popularity": {
        "field_name": "popularity",
        "freshness_hours": 24.0
    },
    "explicit_flag": {
        "field_name": "explicit",
        "freshness_hours": 24.0
    }
})

__all__ = [
    "SpotifyConnector",
    "convert_spotify_playlist_to_connector",
    "convert_spotify_track_to_connector",
    "extract_spotify_track_uris",
    "get_connector_config",
]