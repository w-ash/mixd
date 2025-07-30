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

from typing import Any, Protocol

from src.domain.entities.track import Track, TrackList


class ConfigProvider(Protocol):
    """Abstracts configuration access for testing and deployment flexibility."""

    def get(self, key: str, default: Any = None) -> Any:
        """Retrieve configuration value by key."""
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

    async def execute_use_case(self, use_case_getter: Any, command: Any) -> Any:
        """Execute business logic with automatic transaction management.

        Args:
            use_case_getter: Async function that returns configured use case
            command: Command object containing operation parameters

        Returns:
            Result from use case execution
        """
        ...


class TransformFunction(Protocol):
    """Pure track list transformations for functional composition."""

    def __call__(self, track_list: TrackList, context: dict[str, Any]) -> TrackList:
        """Apply transformation to track list."""
        ...


class WorkflowNode(Protocol):
    """Contract for workflow execution steps enabling declarative composition."""

    async def execute(self, context: WorkflowContext, **kwargs: Any) -> Any:
        """Execute workflow step with access to all dependencies."""
        ...


class WorkflowNodeFactory(Protocol):
    """Creates workflow nodes for dynamic workflow construction from configuration."""

    def create_source_node(self, node_type: str, **config: Any) -> WorkflowNode:
        """Create data source node (playlist, album, library, play history)."""
        ...

    def create_transform_node(self, transform_name: str, **config: Any) -> WorkflowNode:
        """Create transformation node (filter, sort, enrich, dedupe)."""
        ...

    def create_destination_node(
        self, destination_type: str, **config: Any
    ) -> WorkflowNode:
        """Create output destination node (playlist update, file export)."""
        ...
