"""Protocol definitions for workflow dependency injection.

These protocols define contracts for external dependencies needed by workflows,
enabling Clean Architecture compliance through dependency inversion.
"""

from typing import Any, Protocol

from src.domain.entities.track import Track, TrackList

# RepositoryProvider removed - Clean Architecture uses dependency injection through use cases


class ConfigProvider(Protocol):
    """Protocol for configuration access."""

    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration value by key."""
        ...


class LoggerProvider(Protocol):
    """Protocol for logging services."""

    def info(self, message: str, **kwargs: Any) -> None:
        """Log info message."""
        ...

    def debug(self, message: str, **kwargs: Any) -> None:
        """Log debug message."""
        ...

    def warning(self, message: str, **kwargs: Any) -> None:
        """Log warning message."""
        ...

    def error(self, message: str, **kwargs: Any) -> None:
        """Log error message."""
        ...


class ConnectorProvider(Protocol):
    """Protocol for external service connectors."""

    async def get_tracks(self, **kwargs: Any) -> list[Track]:
        """Get tracks from external service."""
        ...

    async def get_playlists(self, **kwargs: Any) -> list[Any]:
        """Get playlists from external service."""
        ...


class ConnectorRegistry(Protocol):
    """Protocol for connector management."""

    def get_connector(self, name: str) -> ConnectorProvider:
        """Get connector by name."""
        ...

    def list_connectors(self) -> list[str]:
        """List available connector names."""
        ...


class DatabaseSessionProvider(Protocol):
    """Protocol for database session management."""

    def get_session(self) -> Any:
        """Get database session context manager."""
        ...


class UseCaseProvider(Protocol):
    """Protocol for providing configured use cases with dependency injection."""

    async def get_create_canonical_playlist_use_case(self) -> Any:
        """Get CreateCanonicalPlaylistUseCase with injected dependencies."""
        ...

    async def get_create_connector_playlist_use_case(self) -> Any:
        """Get CreateConnectorPlaylistUseCase with injected dependencies."""
        ...

    async def get_track_identity_use_case(self) -> Any:
        """Get ResolveTrackIdentityUseCase with injected dependencies."""
        ...

    async def get_enrich_tracks_use_case(self) -> Any:
        """Get EnrichTracksUseCase with injected dependencies."""
        ...

    async def get_match_tracks_use_case(self) -> Any:
        """Get MatchTracksUseCase with injected dependencies."""
        ...


class WorkflowContext(Protocol):
    """Complete workflow execution context with all dependencies."""

    @property
    def config(self) -> ConfigProvider:
        """Configuration provider."""
        ...

    @property
    def logger(self) -> LoggerProvider:
        """Logger provider."""
        ...

    @property
    def connectors(self) -> ConnectorRegistry:
        """Connector registry."""
        ...

    @property
    def use_cases(self) -> UseCaseProvider:
        """Use case provider with dependency injection."""
        ...

    @property
    def session_provider(self) -> DatabaseSessionProvider:
        """Database session provider."""
        ...

    async def execute_use_case(self, use_case_getter: Any, command: Any) -> Any:
        """Execute use case with UnitOfWork pattern.

        This method provides a single entry point for all workflow use case execution,
        handling UnitOfWork creation, session management, and cleanup automatically.

        Args:
            use_case_getter: Async function that returns a use case instance
            command: Command object to pass to the use case

        Returns:
            Result from use case execution
        """
        ...


class TransformFunction(Protocol):
    """Protocol for workflow transform functions."""

    def __call__(self, track_list: TrackList, context: dict[str, Any]) -> TrackList:
        """Apply transformation to track list."""
        ...


class WorkflowNode(Protocol):
    """Protocol for workflow execution nodes."""

    async def execute(self, context: WorkflowContext, **kwargs: Any) -> Any:
        """Execute workflow node with given context."""
        ...


class WorkflowNodeFactory(Protocol):
    """Protocol for creating workflow nodes."""

    def create_source_node(self, node_type: str, **config: Any) -> WorkflowNode:
        """Create source node by type."""
        ...

    def create_transform_node(self, transform_name: str, **config: Any) -> WorkflowNode:
        """Create transform node by name."""
        ...

    def create_destination_node(
        self, destination_type: str, **config: Any
    ) -> WorkflowNode:
        """Create destination node by type."""
        ...
