"""Factory functions for creating Spotify services.

Contains Spotify-specific factory logic isolated in the spotify connector directory.
Implements clean architecture by providing creation functions for all Spotify services
without exposing Spotify internals to other layers.
"""

from src.domain.repositories.play import PlayImporterProtocol

from .play_resolver import SpotifyConnectorPlayResolver


def create_play_importer() -> PlayImporterProtocol:
    """Create Spotify-specific play importer.

    Returns:
        Configured SpotifyPlayImporter implementing PlayImporterProtocol
    """
    from .play_importer import SpotifyPlayImporter

    return SpotifyPlayImporter()


def create_play_resolver() -> SpotifyConnectorPlayResolver:
    """Create Spotify-specific play resolver.

    Returns:
        Configured SpotifyConnectorPlayResolver
    """
    from .connector import SpotifyConnector

    return SpotifyConnectorPlayResolver(spotify_connector=SpotifyConnector())
