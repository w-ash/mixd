"""WorkflowContext implementation for dependency injection.

Provides concrete implementations of all workflow dependencies following
Clean Architecture principles.
"""

from dataclasses import dataclass
from typing import Any

from src.config import get_logger

# Repository interfaces imported only where needed by use case providers
from src.infrastructure.connectors import CONNECTORS, discover_connectors
from src.infrastructure.persistence.database.db_connection import get_session

# Repository factory functions will be imported locally where needed
from .protocols import (
    ConfigProvider,
    ConnectorRegistry,
    DatabaseSessionProvider,
    LoggerProvider,
    UseCaseProvider,
    WorkflowContext,
)


class ConfigProviderImpl:
    """Configuration provider implementation."""

    def __init__(self):
        """Initialize configuration provider."""
        from src.config import get_config as _get_config

        self._get_config = _get_config

    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration value by key."""
        return self._get_config(key, default)


class LoggerProviderImpl:
    """Logger provider implementation."""

    def __init__(self, name: str = __name__):
        """Initialize logger provider."""
        self._logger = get_logger(name)

    def info(self, message: str, **kwargs: Any) -> None:
        """Log info message."""
        self._logger.info(message, **kwargs)

    def debug(self, message: str, **kwargs: Any) -> None:
        """Log debug message."""
        self._logger.debug(message, **kwargs)

    def warning(self, message: str, **kwargs: Any) -> None:
        """Log warning message."""
        self._logger.warning(message, **kwargs)

    def error(self, message: str, **kwargs: Any) -> None:
        """Log error message."""
        self._logger.error(message, **kwargs)


# WorkflowConnectorAdapter removed - violates 2025 clean architecture principles
# Connectors are now injected directly without wrapper adapters


class ConnectorRegistryImpl:
    """Connector registry implementation using direct connector access."""

    def __init__(self):
        """Initialize connector registry."""
        discover_connectors()
        self._connectors = CONNECTORS

    def get_connector(self, name: str):
        """Get connector by name - direct access without provider wrapper."""
        if name not in self._connectors:
            raise ValueError(f"Unknown connector: {name}")

        connector_config = self._connectors[name]
        return connector_config["factory"]({})

    def list_connectors(self) -> list[str]:
        """List available connector names."""
        return list(self._connectors.keys())


class DatabaseSessionProviderImpl:
    """Database session provider implementation."""

    def get_session(self):
        """Get database session."""
        return get_session()


class SharedSessionProvider:
    """Shared session provider for workflow-scoped database access.

    This provider manages a single AsyncSession that is shared across
    all workflow tasks to prevent SQLite database locks caused by
    concurrent sessions.
    """

    def __init__(self, session):
        """Initialize with a shared session."""
        self._session = session

    def get_session(self):
        """Get the shared session (already opened)."""
        return self._session

    async def __aenter__(self):
        """Async context manager entry - session already opened."""
        return self._session

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit - do nothing, session managed by workflow."""


class UseCaseProviderImpl:
    """Use case provider implementation with dependency injection."""

    def __init__(self, shared_session=None):
        """Initialize with optional shared session."""
        self._shared_session = shared_session

    async def get_create_canonical_playlist_use_case(self):
        """Get CreateCanonicalPlaylistUseCase with UnitOfWork pattern."""
        from src.application.use_cases.create_canonical_playlist import (
            CreateCanonicalPlaylistUseCase,
        )

        # Simple instantiation - no dependencies
        # UnitOfWork will be passed as parameter during execution
        return CreateCanonicalPlaylistUseCase()

    async def get_create_connector_playlist_use_case(self):
        """Get CreateConnectorPlaylistUseCase with UnitOfWork pattern."""
        from src.application.use_cases.create_connector_playlist import (
            CreateConnectorPlaylistUseCase,
        )

        # Simple instantiation - no dependencies
        # UnitOfWork will be passed as parameter during execution
        return CreateConnectorPlaylistUseCase()

    async def get_track_identity_use_case(self):
        """Get ResolveTrackIdentityUseCase with UnitOfWork pattern."""
        from src.application.use_cases.resolve_track_identity import (
            ResolveTrackIdentityUseCase,
        )

        # Simple instantiation - no dependencies
        # UnitOfWork will be passed as parameter during execution
        return ResolveTrackIdentityUseCase()

    async def get_enrich_tracks_use_case(self):
        """Get EnrichTracksUseCase with injected dependencies."""
        from src.application.use_cases.enrich_tracks import EnrichTracksUseCase

        # EnrichTracksUseCase follows UnitOfWork pattern - no constructor dependencies
        return EnrichTracksUseCase()

    async def get_match_tracks_use_case(self):
        """Get MatchTracksUseCase with UnitOfWork pattern."""
        from src.application.use_cases.match_tracks import MatchTracksUseCase

        # Simple instantiation - no dependencies
        # UnitOfWork will be passed as parameter during execution
        return MatchTracksUseCase()

    async def get_update_canonical_playlist_use_case(self):
        """Get UpdateCanonicalPlaylistUseCase with UnitOfWork pattern."""
        from src.application.use_cases.update_canonical_playlist import (
            UpdateCanonicalPlaylistUseCase,
        )

        # Simple instantiation - no dependencies
        # UnitOfWork will be passed as parameter during execution
        return UpdateCanonicalPlaylistUseCase()

    async def get_update_connector_playlist_use_case(self):
        """Get UpdateConnectorPlaylistUseCase with UnitOfWork pattern."""
        from src.application.use_cases.update_connector_playlist import (
            UpdateConnectorPlaylistUseCase,
        )

        # Simple instantiation - no dependencies
        # UnitOfWork will be passed as parameter during execution
        return UpdateConnectorPlaylistUseCase()

    async def get_read_canonical_playlist_use_case(self):
        """Get ReadCanonicalPlaylistUseCase with UnitOfWork pattern."""
        from src.application.use_cases.read_canonical_playlist import (
            ReadCanonicalPlaylistUseCase,
        )

        # Simple instantiation - no dependencies
        # UnitOfWork will be passed as parameter during execution
        return ReadCanonicalPlaylistUseCase()


# RepositoryProviderImpl removed - Clean Architecture: use cases handle dependency injection


@dataclass
class ConcreteWorkflowContext:
    """Concrete implementation of WorkflowContext."""

    config: ConfigProvider
    logger: LoggerProvider
    connectors: ConnectorRegistry
    use_cases: UseCaseProvider
    session_provider: DatabaseSessionProvider

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
        # Import UnitOfWork factory locally to avoid circular imports
        from src.infrastructure.persistence.repositories.factories import (
            get_unit_of_work,
        )
        
        # Get session from session provider
        async with self.session_provider.get_session() as session:

            # Create UnitOfWork from session
            uow = get_unit_of_work(session)

            # Get use case instance
            use_case = await use_case_getter()

            # Execute use case with command and UnitOfWork
            return await use_case.execute(command, uow)


def create_workflow_context(shared_session=None) -> WorkflowContext:
    """Create a WorkflowContext with real dependencies wired up."""
    config = ConfigProviderImpl()
    logger = LoggerProviderImpl()
    connectors = ConnectorRegistryImpl()
    session_provider = DatabaseSessionProviderImpl()
    use_cases = UseCaseProviderImpl(shared_session)

    # Repository provider removed - use cases handle their own dependency injection
    # Clean Architecture: Context provides use cases, not repositories directly

    return ConcreteWorkflowContext(
        config=config,
        logger=logger,
        connectors=connectors,
        use_cases=use_cases,
        session_provider=session_provider,
    )
