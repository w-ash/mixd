"""Factory functions for creating Last.fm services.

Contains Last.fm-specific factory logic isolated in the lastfm connector directory.
Implements clean architecture by providing creation functions for all Last.fm services
without exposing Last.fm internals to other layers.
"""

# pyright: reportExplicitAny=false, reportAny=false
# Legitimate Any: API response data, framework types

from typing import Any

from src.domain.repositories import PlayImporterProtocol


def create_play_importer() -> PlayImporterProtocol:
    """Create Last.fm-specific play importer.

    Returns:
        Configured LastfmPlayImporter implementing PlayImporterProtocol
    """
    from .connector import LastFMConnector
    from .play_importer import LastfmPlayImporter

    return LastfmPlayImporter(
        lastfm_connector=LastFMConnector(),
    )


def create_play_resolver() -> Any:
    """Create Last.fm-specific play resolver.

    Returns:
        Configured LastfmConnectorPlayResolver
    """
    from src.infrastructure.connectors.spotify import SpotifyConnector
    from src.infrastructure.connectors.spotify.cross_discovery import (
        SpotifyCrossDiscoveryProvider,
    )

    from .play_resolver import LastfmConnectorPlayResolver
    from .track_resolution_service import LastfmTrackResolutionService

    cross_discovery = SpotifyCrossDiscoveryProvider(
        spotify_connector=SpotifyConnector(),
    )
    return LastfmConnectorPlayResolver(
        lastfm_resolution_service=LastfmTrackResolutionService(
            cross_discovery=cross_discovery,
        )
    )
