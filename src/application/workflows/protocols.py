"""Protocol definitions for workflow dependency injection.

These protocols define the contracts between the application and infrastructure
layers, implementing dependency inversion for Clean Architecture compliance.
Workflows depend on these abstractions rather than concrete implementations,
enabling cross-service operations without tight coupling.

The protocols allow infrastructure components (database repositories, API
connectors, external services) to be swapped without changing workflow logic
and enable comprehensive testing through dependency injection. They aggregate
into WorkflowContext, which provides unified access to all workflow dependencies.
"""

from typing import Any, Protocol, TypedDict

from src.domain.entities.track import TrackList


class ConnectorRegistry(Protocol):
    """Dynamic access to multiple music service connectors (Spotify, Last.fm, MusicBrainz)."""

    def get_connector(self, name: str) -> Any:
        """Get specific music service connector."""
        ...

    def list_connectors(self) -> list[str]:
        """Get names of all available connectors."""
        ...

    async def aclose(self) -> None:
        """Close all cached connector instances and their connection pools."""
        ...


class UseCaseProvider(Protocol):
    """Provides business logic use cases with dependency injection."""

    async def get_create_canonical_playlist_use_case(self) -> Any:
        """Get use case for creating master playlist definitions."""
        ...

    async def get_create_connector_playlist_use_case(self) -> Any:
        """Get use case for creating service-specific playlists."""
        ...

    async def get_enrich_tracks_use_case(self) -> Any:
        """Get use case for enriching tracks with cross-service metadata."""
        ...

    async def get_read_canonical_playlist_use_case(self) -> Any:
        """Get use case for reading master playlist definitions."""
        ...

    async def get_update_canonical_playlist_use_case(self) -> Any:
        """Get use case for updating master playlist definitions."""
        ...

    async def get_update_connector_playlist_use_case(self) -> Any:
        """Get use case for updating service-specific playlists."""
        ...


class WorkflowContext(Protocol):
    """Central dependency container enabling complex cross-service operations."""

    @property
    def connectors(self) -> ConnectorRegistry:
        """Music service API access."""
        ...

    @property
    def use_cases(self) -> UseCaseProvider:
        """Business logic with transaction control."""
        ...

    @property
    def metric_config(self) -> MetricConfigProvider:
        """Metric registry access for enricher configuration."""
        ...

    async def execute_service(self, service_fn: Any, /) -> Any:
        """Execute an async operation with a UnitOfWork.

        General-purpose method for service calls or any async function
        that needs a UoW without the caller importing infrastructure.

        Args:
            service_fn: Async callable receiving a UoW and returning a result.

        Returns:
            Result from the service function.
        """
        ...

    async def execute_use_case(self, use_case_getter: Any, command: Any) -> Any:
        """Execute business logic with automatic transaction management.

        Args:
            use_case_getter: Async function that returns configured use case
            command: Command object containing operation parameters

        Returns:
            Result from use case execution
        """
        ...


class MetricConfigProvider(Protocol):
    """Abstracts metric registry access so application never imports infrastructure."""

    def get_connector_metrics(self, connector: str) -> list[str]:
        """Return metric names supported by a connector."""
        ...

    def get_field_name(self, metric: str) -> str:
        """Map metric name to the connector field name."""
        ...

    def get_metric_freshness(self, metric: str) -> float:
        """Return freshness period in hours for a metric."""
        ...

    def get_all_connectors_metrics(self) -> dict[str, list[str]]:
        """Return all registered connectors and their metric names."""
        ...

    def get_all_field_mappings(self) -> dict[str, str]:
        """Return mapping of all metric names to their field names."""
        ...


class NodeResult(TypedDict):
    """Minimum contract for all workflow node outputs.

    Every node (source, transform, destination) returns at least a tracklist.
    Destination nodes also return operation-specific keys; structural typing
    allows those extra keys while enforcing the tracklist contract.
    """

    tracklist: TrackList
