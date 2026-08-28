"""Factory functions for creating Last.fm services.

Contains Last.fm-specific factory logic isolated in the lastfm connector directory.
Implements clean architecture by providing creation functions for all Last.fm services
without exposing Last.fm internals to other layers.
"""

from src.domain.matching.protocols import CrossDiscoveryProvider
from src.domain.repositories.play import PlayImporterProtocol

from .play_resolver import LastfmConnectorPlayResolver


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


def _discover_cross_discovery() -> CrossDiscoveryProvider | None:
    """First cross-discovery provider any connector declares, or None.

    Reads the ``cross_discovery_factory`` declaration (today Spotify's) off
    the connector registry, so this package names no other connector. No
    declared provider degrades to None — the resolver then skips
    cross-service discovery.
    """
    from src.infrastructure.connectors.discovery import discover_connectors

    for config in discover_connectors().values():
        factory = config.get("cross_discovery_factory")
        if factory is not None:
            return factory()
    return None


def create_play_resolver() -> LastfmConnectorPlayResolver:
    """Create Last.fm-specific play resolver.

    The resolver adopts the discovered cross-discovery provider — no other
    reference survives this call, so the resolver's ``aclose`` is the
    provider's only teardown.

    Returns:
        Configured LastfmConnectorPlayResolver
    """
    return LastfmConnectorPlayResolver(cross_discovery=_discover_cross_discovery())
