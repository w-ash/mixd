"""Dependency injection container for playlist workflow operations.

Manages configuration, logging, music service connectors, database sessions,
and business logic use cases needed for playlist synchronization workflows.
"""

from dataclasses import dataclass
from typing import Any

from src.config import get_logger

# Repository interfaces imported only where needed by use case providers
from src.infrastructure.connectors import discover_connectors
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
    """Provides access to application configuration values.

    Provides workflow access to application settings through a clean interface
    that avoids direct coupling to the config module structure.
    """

    def __init__(self):
        """Initialize the configuration provider."""
        from src.config import settings

        self._settings = settings

    @property
    def settings(self):
        """Direct access to settings for modern usage."""
        return self._settings

    def get(self, key: str, default: Any = None) -> Any:
        """Retrieve a configuration value by key.

        This method is primarily for workflow-specific configuration,
        not application settings. For application settings, use the
        settings property directly.

        Args:
            key: Configuration key to look up
            default: Value to return if key is not found

        Returns:
            Configuration value or default if not found
        """
        # This is for workflow-specific config, not app settings
        # Currently returns default since no workflow config storage is implemented
        _ = key  # Mark as intentionally unused for now
        return default


class LoggerProviderImpl:
    """Provides structured logging for workflow operations.

    Wraps the application logger to provide consistent logging interface
    for tracking workflow progress, errors, and debugging information.
    """

    def __init__(self, name: str = __name__):
        """Initialize the logger provider.

        Args:
            name: Logger name, defaults to current module
        """
        self._logger = get_logger(name)

    def info(self, message: str, **kwargs: Any) -> None:
        """Log informational message.

        Args:
            message: Message to log
            **kwargs: Additional structured data to include
        """
        self._logger.info(message, **kwargs)

    def debug(self, message: str, **kwargs: Any) -> None:
        """Log debug message for troubleshooting.

        Args:
            message: Message to log
            **kwargs: Additional structured data to include
        """
        self._logger.debug(message, **kwargs)

    def warning(self, message: str, **kwargs: Any) -> None:
        """Log warning message for potential issues.

        Args:
            message: Message to log
            **kwargs: Additional structured data to include
        """
        self._logger.warning(message, **kwargs)

    def error(self, message: str, **kwargs: Any) -> None:
        """Log error message for failures.

        Args:
            message: Message to log
            **kwargs: Additional structured data to include
        """
        self._logger.error(message, **kwargs)


class ConnectorRegistryImpl:
    """Registry for music service API connectors.

    Manages access to connectors for music services like Spotify, Last.fm,
    and MusicBrainz. Automatically discovers available connectors and provides
    factory access to create configured connector instances.
    """

    def __init__(self):
        """Initialize connector registry and discover available connectors."""
        self._connectors = discover_connectors()

    def get_connector(self, name: str):
        """Create a connector instance for the specified music service.

        Args:
            name: Name of the connector (e.g., 'spotify', 'lastfm')

        Returns:
            Configured connector instance

        Raises:
            ValueError: If connector name is not registered
        """
        if name not in self._connectors:
            raise ValueError(f"Unknown connector: {name}")

        connector_config = self._connectors[name]
        return connector_config["factory"]({})

    def list_connectors(self) -> list[str]:
        """List names of all available music service connectors.

        Returns:
            List of connector names
        """
        return list(self._connectors.keys())


class DatabaseSessionProviderImpl:
    """Provides database sessions for playlist data operations.

    Creates new database sessions for accessing playlist, track, and
    synchronization data stored in the application database.
    """

    def get_session(self):
        """Create a new database session.

        Returns:
            New database session for data operations
        """
        return get_session()


class SharedSessionProvider:
    """Manages a single database session shared across workflow tasks.

    Prevents SQLite database locks by ensuring all tasks in a workflow
    use the same database session instead of creating concurrent sessions
    that would conflict with SQLite's locking behavior.
    """

    def __init__(self, session):
        """Initialize with an existing database session.

        Args:
            session: Pre-opened database session to share
        """
        self._session = session

    def get_session(self):
        """Get the shared database session.

        Returns:
            The pre-opened shared session
        """
        return self._session

    async def __aenter__(self):
        """Return the shared session for async context management.

        Returns:
            The shared database session
        """
        return self._session

    async def __aexit__(self, exc_type, _exc_val, _exc_tb):
        """Exit async context without closing session.

        The session is managed by the workflow and should not be closed here.
        """


class UseCaseProviderImpl:
    """Factory for playlist and track management business logic.

    Creates instances of use cases that handle playlist operations like
    creating playlists, matching tracks between services, enriching track
    metadata, and synchronizing playlists across music services.
    """

    def __init__(self, shared_session=None):
        """Initialize the use case provider.

        Args:
            shared_session: Optional shared database session for workflows
        """
        self._shared_session = shared_session

    async def get_create_canonical_playlist_use_case(self):
        """Create use case for creating master playlist definitions.

        Returns:
            Use case instance for creating canonical playlists
        """
        from src.application.use_cases.create_canonical_playlist import (
            CreateCanonicalPlaylistUseCase,
        )

        # Simple instantiation - no dependencies
        # UnitOfWork will be passed as parameter during execution
        return CreateCanonicalPlaylistUseCase()

    async def get_create_connector_playlist_use_case(self):
        """Create use case for creating service-specific playlist instances.

        Returns:
            Use case instance for creating connector playlists
        """
        from src.application.use_cases.create_connector_playlist import (
            CreateConnectorPlaylistUseCase,
        )

        # Simple instantiation - no dependencies
        # UnitOfWork will be passed as parameter during execution
        return CreateConnectorPlaylistUseCase()

    async def get_enrich_tracks_use_case(self):
        """Create use case for enriching track metadata from external services.

        Returns:
            Use case instance for track enrichment
        """
        from src.application.use_cases.enrich_tracks import EnrichTracksUseCase

        # EnrichTracksUseCase follows UnitOfWork pattern - no constructor dependencies
        return EnrichTracksUseCase()

    async def get_match_and_identify_tracks_use_case(self):
        """Create use case for track matching and identification.

        Returns:
            Use case instance for track matching and identification
        """
        from src.application.use_cases.match_and_identify_tracks import (
            MatchAndIdentifyTracksUseCase,
        )

        return MatchAndIdentifyTracksUseCase()

    async def get_match_tracks_use_case(self):
        """Create use case for track matching (legacy method name compatibility).

        Returns the new MatchAndIdentifyTracksUseCase for backward compatibility.

        Returns:
            Use case instance for track matching and identification
        """
        return await self.get_match_and_identify_tracks_use_case()

    async def get_update_canonical_playlist_use_case(self):
        """Create use case for updating master playlist definitions.

        Returns:
            Use case instance for updating canonical playlists
        """
        from src.application.use_cases.update_canonical_playlist import (
            UpdateCanonicalPlaylistUseCase,
        )

        # Simple instantiation - no dependencies
        # UnitOfWork will be passed as parameter during execution
        return UpdateCanonicalPlaylistUseCase()

    async def get_update_connector_playlist_use_case(self):
        """Create use case for updating service-specific playlist instances.

        Returns:
            Use case instance for updating connector playlists
        """
        from src.application.use_cases.update_connector_playlist import (
            UpdateConnectorPlaylistUseCase,
        )

        # Simple instantiation - no dependencies
        # UnitOfWork will be passed as parameter during execution
        return UpdateConnectorPlaylistUseCase()

    async def get_read_canonical_playlist_use_case(self):
        """Create use case for reading master playlist definitions.

        Returns:
            Use case instance for reading canonical playlists
        """
        from src.application.use_cases.read_canonical_playlist import (
            ReadCanonicalPlaylistUseCase,
        )

        # Simple instantiation - no dependencies
        # UnitOfWork will be passed as parameter during execution
        return ReadCanonicalPlaylistUseCase()


@dataclass
class ConcreteWorkflowContext:
    """Central dependency container for playlist workflow operations.

    Aggregates all services needed for playlist synchronization workflows
    including configuration, logging, music service connectors, database
    access, and business logic use cases.
    """

    config: ConfigProvider
    logger: LoggerProvider
    connectors: ConnectorRegistry
    use_cases: UseCaseProvider
    session_provider: DatabaseSessionProvider
    shared_session: Any = (
        None  # Optional shared session for workflow transaction boundaries
    )

    async def execute_use_case(self, use_case_getter: Any, command: Any) -> Any:
        """Execute a business logic use case with proper resource management.

        Handles database session lifecycle, unit of work creation, and
        cleanup automatically for any workflow use case execution.

        If a shared session is available, uses that instead of creating a new one.
        This prevents transaction boundary issues when multiple use cases need
        to operate on the same data within a single workflow.

        Args:
            use_case_getter: Async function that returns a use case instance
            command: Command object containing operation parameters

        Returns:
            Result from the executed use case
        """
        # Import UnitOfWork factory locally to avoid circular imports
        from src.infrastructure.persistence.repositories.factories import (
            get_unit_of_work,
        )

        # Use shared session if available, otherwise create new one
        if self.shared_session is not None:
            # Use shared session (transaction managed by caller)
            uow = get_unit_of_work(self.shared_session)

            # Get use case instance
            use_case = await use_case_getter()

            # Execute use case with shared UnitOfWork (don't manage transaction here)
            return await use_case.execute(command, uow)
        else:
            # Create new session for this use case (legacy behavior)
            async with self.session_provider.get_session() as session:
                # Create UnitOfWork from session
                uow = get_unit_of_work(session)

                # Get use case instance
                use_case = await use_case_getter()

                # Execute use case with command and UnitOfWork
                return await use_case.execute(command, uow)


def create_workflow_context(shared_session=None) -> WorkflowContext:
    """Create a complete workflow context with all dependencies configured.

    Factory function that instantiates and wires together all the services
    needed for playlist workflow operations including configuration, logging,
    music service connectors, database access, and business logic.

    Args:
        shared_session: Optional pre-opened database session to share

    Returns:
        Configured workflow context ready for use
    """
    config = ConfigProviderImpl()
    logger = LoggerProviderImpl()
    connectors = ConnectorRegistryImpl()
    session_provider = DatabaseSessionProviderImpl()
    use_cases = UseCaseProviderImpl(shared_session)

    return ConcreteWorkflowContext(
        config=config,
        logger=logger,
        connectors=connectors,
        use_cases=use_cases,
        session_provider=session_provider,
        shared_session=shared_session,
    )
