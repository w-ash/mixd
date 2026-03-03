"""Factory functions for creating Spotify services.

Contains Spotify-specific factory logic isolated in the spotify connector directory.
Implements clean architecture by providing creation functions for all Spotify services
without exposing Spotify internals to other layers.
"""

# pyright: reportExplicitAny=false, reportAny=false
# Legitimate Any: API response data, framework types

from typing import Any

from src.domain.repositories import PlayImporterProtocol


def create_play_importer() -> PlayImporterProtocol:
    """Create Spotify-specific play importer.

    Returns:
        Configured SpotifyPlayImporter implementing PlayImporterProtocol
    """
    from .play_importer import SpotifyPlayImporter

    return SpotifyPlayImporter()


def create_play_resolver() -> Any:
    """Create Spotify-specific play resolver.

    Returns:
        Configured SpotifyConnectorPlayResolver
    """
    from .connector import SpotifyConnector
    from .play_resolver import SpotifyConnectorPlayResolver

    return SpotifyConnectorPlayResolver(spotify_connector=SpotifyConnector())
