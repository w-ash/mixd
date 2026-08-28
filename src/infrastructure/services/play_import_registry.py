"""Play importer/resolver lookup over the connector registry.

Connectors declare their play factories on ``ConnectorConfig``
(``play_importer_factories``, ``play_resolver_factory``), and this registry
resolves ``(service, kind)`` requests against those declarations — a new
connector wires its play channels by editing only its own package.
"""

from src.domain.repositories.play import (
    ImportKind,
    PlayImporterProtocol,
    PlayResolverProtocol,
)
from src.domain.repositories.uow import UnitOfWorkProtocol
from src.infrastructure.connectors.discovery import discover_connectors
from src.infrastructure.connectors.protocols import (
    ConnectorConfig,
    PlayImporterFactory,
    PlayResolverFactory,
)

__all__ = ["ImportKind", "PlayImportServiceRegistry", "get_play_import_registry"]


def _play_service_name(name: str, config: ConnectorConfig) -> str:
    """Data-plane service name a connector's play rows key on."""
    return config.get("play_service_name", name)


class PlayImportServiceRegistry:
    """Resolves play importers and resolvers from connector declarations.

    Importers key on ``(service, ImportKind)``; resolvers stay per-service,
    because resolution depends on the identifiers a service mints, not on how
    rows arrived. Lookups read ``discover_connectors()`` per call — the
    discovery cache makes that cheap, and no table here can go stale.
    """

    def _importer_factories(self) -> dict[tuple[str, ImportKind], PlayImporterFactory]:
        """Declared ``(service, kind)`` → importer factory across all connectors."""
        return {
            (_play_service_name(name, config), kind): factory
            for name, config in discover_connectors().items()
            for kind, factory in config.get("play_importer_factories", {}).items()
        }

    def _resolver_factories(self) -> dict[str, PlayResolverFactory]:
        """Declared service → resolver factory across all connectors."""
        return {
            _play_service_name(name, config): factory
            for name, config in discover_connectors().items()
            if (factory := config.get("play_resolver_factory")) is not None
        }

    async def create_play_importer(
        self, service: str, kind: ImportKind, uow: UnitOfWorkProtocol
    ) -> PlayImporterProtocol:
        """Importer for ``service`` reading from ``kind`` (live API or export file).

        Raises ValueError when no connector declares the combination.
        """
        # The PlayImportProvider protocol carries a uow; config-declared
        # factories are zero-arg, so nothing here consumes it.
        del uow
        factories = self._importer_factories()
        factory = factories.get((service, kind))
        if factory is None:
            supported = ", ".join(f"{svc}:{knd}" for svc, knd in sorted(factories))
            raise ValueError(
                f"Unsupported import '{service}:{kind}'. Supported: {supported}"
            )
        return factory()

    async def create_play_resolver(
        self, service: str, uow: UnitOfWorkProtocol | None = None
    ) -> PlayResolverProtocol:
        """Resolver for ``service`` — how its identifiers map to canonical tracks.

        Raises ValueError when no connector declares one.
        """
        del uow
        factories = self._resolver_factories()
        factory = factories.get(service)
        if factory is None:
            supported = ", ".join(sorted(factories))
            raise ValueError(
                f"Unsupported service '{service}'. Supported services: {supported}"
            )
        return factory()


# Global registry instance for easy access
_registry_instance: PlayImportServiceRegistry | None = None


def get_play_import_registry() -> PlayImportServiceRegistry:
    """Get the shared play import service registry instance."""
    global _registry_instance
    if _registry_instance is None:
        _registry_instance = PlayImportServiceRegistry()
    return _registry_instance
