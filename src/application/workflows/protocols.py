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

from src.domain.entities.track import Track, TrackList


class ConfigProvider(Protocol):
    """Abstracts configuration access for testing and deployment flexibility."""

    ...


class LoggerProvider(Protocol):
    """Abstracts logging for testable structured logging with context."""

    def info(self, message: str, **kwargs: Any) -> None:
        """Log informational message with optional context."""
        ...

    def debug(self, message: str, **kwargs: Any) -> None:
        """Log debug message with optional context."""
        ...

    def warning(self, message: str, **kwargs: Any) -> None:
        """Log warning message with optional context."""
        ...

    def error(self, message: str, **kwargs: Any) -> None:
        """Log error message with optional context."""
        ...


class ConnectorProvider(Protocol):
    """Abstracts music service connections for unified cross-service operations."""

    async def get_tracks(self, **kwargs: Any) -> list[Track]:
        """Retrieve tracks from external music service."""
        ...

    async def get_playlists(self, **kwargs: Any) -> list[Any]:
        """Retrieve playlists from external music service."""
        ...


class ConnectorRegistry(Protocol):
    """Dynamic access to multiple music service connectors (Spotify, Last.fm, MusicBrainz)."""

    def get_connector(self, name: str) -> ConnectorProvider:
        """Get specific music service connector."""
        ...

    def list_connectors(self) -> list[str]:
        """Get names of all available connectors."""
        ...


class DatabaseSessionProvider(Protocol):
    """Abstracts database session creation for UnitOfWork pattern."""

    def get_session(self) -> Any:
        """Get database session context manager."""
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

    async def get_match_and_identify_tracks_use_case(self) -> Any:
        """Get use case for matching and identifying tracks between music services."""
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
    def config(self) -> ConfigProvider:
        """Configuration access."""
        ...

    @property
    def logger(self) -> LoggerProvider:
        """Structured logging."""
        ...

    @property
    def connectors(self) -> ConnectorRegistry:
        """Music service API access."""
        ...

    @property
    def use_cases(self) -> UseCaseProvider:
        """Business logic with transaction control."""
        ...

    @property
    def session_provider(self) -> DatabaseSessionProvider:
        """Database access."""
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


class TrackMetadataConnector(Protocol):
    """Protocol for connectors that can fetch complete external track data.

    Provides a unified interface for all connectors to retrieve complete track
    records from external services. Defined at application/domain boundary so
    application code can reference it without importing from infrastructure.
    """

    async def get_external_track_data(
        self, tracks: list[Track]
    ) -> dict[int, dict[str, Any]]:
        """Retrieve complete track data from the external service for multiple tracks."""
        ...


class PlayImportServiceRegistryProtocol(Protocol):
    """Protocol for the play import service registry.

    Abstracts infrastructure registry so application code can request
    importers and resolvers without importing concrete implementations.
    """

    async def create_play_importer(self, service: str, uow: Any) -> Any:
        """Create a play importer for the specified service."""
        ...

    async def create_play_resolver(self, service: str, uow: Any | None = None) -> Any:
        """Create a play resolver for the specified service."""
        ...

    def get_supported_services(self) -> list[str]:
        """Get list of supported service identifiers."""
        ...


class NodeResult(TypedDict):
    """Minimum contract for all workflow node outputs.

    Every node (source, transform, destination) returns at least a tracklist.
    Destination nodes also return operation-specific keys; structural typing
    allows those extra keys while enforcing the tracklist contract.
    """

    tracklist: TrackList
