"""Infrastructure service registry for mapping service names to connector factories.

Implements clean architecture by providing a generic mapping layer that routes
service requests to appropriate connector factories without the application layer
needing to know about specific connectors.
"""

from collections.abc import Callable
from typing import Any

from src.domain.repositories import (
    PlayImporterProtocol,
    PlayResolverProtocol,
    UnitOfWorkProtocol,
)


class PlayImportServiceRegistry:
    """Registry for mapping service names to connector-specific factories.

    Encapsulates the mapping from generic service names (e.g., 'lastfm', 'spotify')
    to specific connector factory implementations, maintaining clean architecture
    boundaries while enabling extensibility.
    """

    _importer_factories: dict[str, Callable[..., Any]]
    _resolver_factories: dict[str, Callable[..., Any]]

    def __init__(self):
        """Initialize registry with known service mappings."""
        self._importer_factories = {
            "lastfm": self._create_lastfm_importer,
            "spotify": self._create_spotify_importer,
        }

        self._resolver_factories = {
            "lastfm": self._create_lastfm_resolver,
            "spotify": self._create_spotify_resolver,
        }

    async def create_play_importer(
        self, service: str, uow: UnitOfWorkProtocol
    ) -> PlayImporterProtocol:
        """Create play importer for the specified service.

        Args:
            service: Service identifier (e.g., 'lastfm', 'spotify')
            uow: Unit of work for repository access

        Returns:
            Service-specific play importer implementing PlayImporterProtocol

        Raises:
            ValueError: If service is not supported
        """
        if service not in self._importer_factories:
            supported_services = ", ".join(self._importer_factories.keys())
            raise ValueError(
                f"Unsupported service '{service}'. Supported services: {supported_services}"
            )

        factory_func = self._importer_factories[service]
        return await factory_func(uow)

    async def create_play_resolver(
        self, service: str, uow: UnitOfWorkProtocol | None = None
    ) -> PlayResolverProtocol:
        """Create play resolver for the specified service.

        Args:
            service: Service identifier (e.g., 'lastfm', 'spotify')
            uow: Unit of work (optional for resolvers)

        Returns:
            Service-specific play resolver

        Raises:
            ValueError: If service is not supported
        """
        if service not in self._resolver_factories:
            supported_services = ", ".join(self._resolver_factories.keys())
            raise ValueError(
                f"Unsupported service '{service}'. Supported services: {supported_services}"
            )

        factory_func = self._resolver_factories[service]
        return await factory_func(uow)

    def get_supported_services(self) -> list[str]:
        """Get list of supported service identifiers.

        Returns:
            List of supported service names
        """
        return list(self._importer_factories.keys())

    # === PRIVATE FACTORY METHODS ===
    # These delegate to connector-specific factories while maintaining clean boundaries

    async def _create_lastfm_importer(
        self, _uow: UnitOfWorkProtocol
    ) -> PlayImporterProtocol:
        """Create Last.fm importer via connector factory."""
        from src.infrastructure.connectors.lastfm.factory import create_play_importer

        return create_play_importer()

    async def _create_spotify_importer(
        self, _uow: UnitOfWorkProtocol
    ) -> PlayImporterProtocol:
        """Create Spotify importer via connector factory."""
        from src.infrastructure.connectors.spotify.factory import create_play_importer

        return create_play_importer()

    async def _create_lastfm_resolver(
        self, _uow: UnitOfWorkProtocol | None = None
    ) -> PlayResolverProtocol:
        """Create Last.fm resolver via connector factory."""
        from src.infrastructure.connectors.lastfm.factory import create_play_resolver

        return create_play_resolver()

    async def _create_spotify_resolver(
        self, _uow: UnitOfWorkProtocol | None = None
    ) -> PlayResolverProtocol:
        """Create Spotify resolver via connector factory."""
        from src.infrastructure.connectors.spotify.factory import create_play_resolver

        return create_play_resolver()


# Global registry instance for easy access
_registry_instance: PlayImportServiceRegistry | None = None


def get_play_import_registry() -> PlayImportServiceRegistry:
    """Get the global play import service registry instance.

    Uses singleton pattern to ensure consistent registry across the application.

    Returns:
        Shared PlayImportServiceRegistry instance
    """
    global _registry_instance
    if _registry_instance is None:
        _registry_instance = PlayImportServiceRegistry()
    return _registry_instance
