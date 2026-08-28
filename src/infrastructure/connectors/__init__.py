"""Service connectors for external music platforms and APIs."""

from src.infrastructure.connectors.discovery import discover_connectors

# Import main connector classes for re-export
from src.infrastructure.connectors.lastfm import (
    LastFMConnector,
    LastFMTrackInfo,
)
from src.infrastructure.connectors.musicbrainz import MusicBrainzConnector
from src.infrastructure.connectors.spotify import (
    SpotifyConnector,
    convert_spotify_playlist_to_connector,
    convert_spotify_track_to_connector,
)

# Define public API with explicit exports
__all__ = [
    "LastFMConnector",
    "LastFMTrackInfo",
    "MusicBrainzConnector",
    "SpotifyConnector",
    "convert_spotify_playlist_to_connector",
    "convert_spotify_track_to_connector",
    "discover_connectors",
]
