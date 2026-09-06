"""The connector-registry access contract, shared across the application layer.

A leaf protocol (no workflow or infrastructure dependencies) so any use case or
service can read the static connector registry — display names, categories,
auth methods, capabilities, external page links — and build connector clients,
without importing infrastructure. The per-user runtime half
(``ConnectorStatus``) stays with the status probes; this is the declarative
half only.
"""

from typing import Protocol

from src.domain.entities.connector import ConnectorDescriptor


class ConnectorCatalog(Protocol):
    """Abstracts connector registry access so application never imports infrastructure."""

    def list_descriptors(self) -> list[ConnectorDescriptor]:
        """Return a descriptor for every registered connector."""
        ...

    def describe(self, name: str) -> ConnectorDescriptor:
        """Return one connector's descriptor.

        Raises:
            ValueError: If the connector is not registered.
        """
        ...

    def create_connector(self, name: str) -> object:
        """Build a new client instance for one connector.

        Returns ``object``: callers narrow via the capability protocols in
        ``application/connector_protocols.py``.

        Raises:
            ValueError: If the connector is not registered.
        """
        ...

    def playlist_url(self, name: str, playlist_id: str) -> str | None:
        """Return the connector's own web page for one of its playlists.

        Returns None when the connector is unregistered or declares no playlist
        link, so callers store nothing rather than a guessed URL.
        """
        ...


class _DiscoveryConnectorCatalog:
    """Catalog backed by infrastructure connector discovery."""

    def _descriptors(self) -> dict[str, ConnectorDescriptor]:
        from src.infrastructure.connectors import discover_connectors

        return {
            name: ConnectorDescriptor(
                name=name,
                display_name=config["display_name"],
                category=config["category"],
                auth_method=config["auth_method"],
                capabilities=config["capabilities"],
            )
            for name, config in discover_connectors().items()
        }

    def list_descriptors(self) -> list[ConnectorDescriptor]:
        return list(self._descriptors().values())

    def describe(self, name: str) -> ConnectorDescriptor:
        descriptor = self._descriptors().get(name)
        if descriptor is None:
            raise ValueError(f"Unknown connector: {name}")
        return descriptor

    def create_connector(self, name: str) -> object:
        from src.infrastructure.connectors import discover_connectors

        config = discover_connectors().get(name)
        if config is None:
            raise ValueError(f"Unknown connector: {name}")
        return config["factory"]()

    def playlist_url(self, name: str, playlist_id: str) -> str | None:
        from src.infrastructure.connectors._shared.external_urls import (
            connector_playlist_url,
        )

        return connector_playlist_url(name, playlist_id)


def default_connector_catalog() -> ConnectorCatalog:
    """Build the default connector catalog (approved infrastructure bridge).

    Function-scoped so the layer edge stays narrow. Lives beside the protocol
    it provides so the bridge has one home rather than a copy per use case —
    the same arrangement as ``metric_config.default_metric_config``.
    """
    return _DiscoveryConnectorCatalog()
