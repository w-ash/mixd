"""Spotify connector module.

Modular Spotify API integration with clear separation of concerns.

Components:
- SpotifyAPIClient: Pure API wrapper with OAuth authentication
- SpotifyOperations: Business logic with delegated playlist sync operations
- SpotifyPlaylistSyncOperations: Differential playlist synchronization
- SpotifyConversions: Data transformation utilities
- SpotifyConnector: Main facade implementing connector protocols
- SpotifyProvider: Track matching provider for identity resolution

Usage:
    from src.infrastructure.connectors.spotify import SpotifyConnector
    connector = SpotifyConnector()
    await connector.create_playlist(...)
"""

from src.infrastructure.connectors.spotify.connector import (
    SpotifyConnector,
    get_connector_config,
)
from src.infrastructure.connectors.spotify.conversions import (
    convert_spotify_playlist_to_connector,
    convert_spotify_track_to_connector,
    extract_spotify_track_uris,
)
from src.infrastructure.connectors.spotify.matching_provider import SpotifyProvider

__all__ = [
    "SpotifyConnector",
    "SpotifyProvider",
    "convert_spotify_playlist_to_connector",
    "convert_spotify_track_to_connector",
    "extract_spotify_track_uris",
    "get_connector_config",
]
