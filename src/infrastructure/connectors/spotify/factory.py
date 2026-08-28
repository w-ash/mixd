"""Factory functions for creating Spotify services.

Contains Spotify-specific factory logic isolated in the spotify connector directory.
Implements clean architecture by providing creation functions for all Spotify services
without exposing Spotify internals to other layers.
"""

from src.domain.matching.protocols import CrossDiscoveryProvider
from src.domain.repositories.play import PlayImporterProtocol

from .play_resolver import SpotifyConnectorPlayResolver


def create_play_importer() -> PlayImporterProtocol:
    """Create Spotify-specific play importer.

    Returns:
        Configured SpotifyPlayImporter implementing PlayImporterProtocol
    """
    from .play_importer import SpotifyPlayImporter

    return SpotifyPlayImporter()


def create_recently_played_importer() -> PlayImporterProtocol:
    """Create the Spotify recently-played API importer.

    Returns:
        Configured SpotifyRecentlyPlayedImporter implementing PlayImporterProtocol
    """
    from .recently_played_importer import SpotifyRecentlyPlayedImporter

    return SpotifyRecentlyPlayedImporter()


def create_play_resolver() -> SpotifyConnectorPlayResolver:
    """Create Spotify-specific play resolver.

    Returns:
        Configured SpotifyConnectorPlayResolver
    """
    from .connector import SpotifyConnector

    return SpotifyConnectorPlayResolver(spotify_connector=SpotifyConnector())


def create_cross_discovery_provider() -> CrossDiscoveryProvider:
    """Create the cross-discovery provider other connectors resolve via discovery.

    Declared as ``cross_discovery_factory`` on the Spotify config so consumers
    (e.g. Last.fm's resolver) find it by declaration instead of importing
    ``SpotifyCrossDiscoveryProvider`` concretely. The ListenBrainz lookup
    arms the optional pre-resolution arm; the provider owns (and closes) it
    and the connector built here.
    """
    from src.infrastructure.connectors.listenbrainz.lookup import ListenBrainzLookup

    from .connector import SpotifyConnector
    from .cross_discovery import SpotifyCrossDiscoveryProvider

    return SpotifyCrossDiscoveryProvider(
        spotify_connector=SpotifyConnector(),
        listenbrainz_lookup=ListenBrainzLookup(),
        owns_connector=True,
    )
