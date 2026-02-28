"""Dependency injection container for playlist workflow operations.

Manages configuration, logging, music service connectors, database sessions,
and business logic use cases needed for playlist synchronization workflows.
"""

from collections.abc import Callable, Coroutine
from types import TracebackType
from typing import TYPE_CHECKING, Any

from attrs import define
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.repositories import UnitOfWorkProtocol

if TYPE_CHECKING:
    from loguru import Logger

from src.config import get_logger

# Approved infrastructure bridge: context.py is a DI container (like runner.py and
# prefect.py). Infrastructure imports for session creation and SQLAlchemy AsyncSession
# are intentional wiring — this is the designated integration point for workflow DI.
from src.infrastructure.persistence.database.db_connection import get_session

# Repository factory functions will be imported locally where needed
from .protocols import (
    ConnectorRegistry,
    DatabaseSessionProvider,
    LoggerProvider,
    UseCaseProvider,
    WorkflowContext,
)


class LoggerProviderImpl:
    """Provides structured logging for workflow operations.

    Wraps the application logger to provide consistent logging interface
    for tracking workflow progress, errors, and debugging information.
    """

    _logger: Logger

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
    and MusicBrainz. Automatically discovers available connectors and caches
    instances so repeated calls return the same connector (same httpx pool).
    """

    _connectors: dict[str, Any]
    _cache: dict[str, Any]

    def __init__(self):
        """Initialize connector registry and discover available connectors."""
        from src.infrastructure.connectors import discover_connectors

        self._connectors = discover_connectors()
        self._cache = {}

    def get_connector(self, name: str):
        """Get (or create) a connector instance for the specified music service.

        Args:
            name: Name of the connector (e.g., 'spotify', 'lastfm')

        Returns:
            Configured connector instance (cached per registry lifetime)

        Raises:
            ValueError: If connector name is not registered
        """
        if name in self._cache:
            return self._cache[name]
        if name not in self._connectors:
            raise ValueError(f"Unknown connector: {name}")

        instance = self._connectors[name]["factory"]({})
        self._cache[name] = instance
        return instance

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

    _session: AsyncSession

    def __init__(self, session: AsyncSession) -> None:
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

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        _exc_val: BaseException | None,
        _exc_tb: TracebackType | None,
    ) -> None:
        """Exit async context without closing session.

        The session is managed by the workflow and should not be closed here.
        """


class UseCaseProviderImpl:
    """Factory for playlist and track management business logic.

    Creates instances of use cases that handle playlist operations like
    creating playlists, matching tracks between services, enriching track
    metadata, and synchronizing playlists across music services.
    """

    _shared_session: AsyncSession | None

    def __init__(self, shared_session: AsyncSession | None = None) -> None:
        """Initialize the use case provider.

        Args:
            shared_session: Optional shared database session for workflows
        """
        self._shared_session = shared_session

    async def get_create_canonical_playlist_use_case(self):
        from src.application.use_cases.create_canonical_playlist import (
            CreateCanonicalPlaylistUseCase,
        )

        return CreateCanonicalPlaylistUseCase()

    async def get_create_connector_playlist_use_case(self):
        from src.application.use_cases.create_connector_playlist import (
            CreateConnectorPlaylistUseCase,
        )

        return CreateConnectorPlaylistUseCase()

    async def get_enrich_tracks_use_case(self):
        from src.application.use_cases.enrich_tracks import EnrichTracksUseCase

        return EnrichTracksUseCase()

    async def get_update_canonical_playlist_use_case(self):
        from src.application.use_cases.update_canonical_playlist import (
            UpdateCanonicalPlaylistUseCase,
        )

        return UpdateCanonicalPlaylistUseCase()

    async def get_update_connector_playlist_use_case(self):
        from src.application.use_cases.update_connector_playlist import (
            UpdateConnectorPlaylistUseCase,
        )

        return UpdateConnectorPlaylistUseCase()

    async def get_read_canonical_playlist_use_case(self):
        from src.application.use_cases.read_canonical_playlist import (
            ReadCanonicalPlaylistUseCase,
        )

        return ReadCanonicalPlaylistUseCase()


@define(slots=True)
class ConcreteWorkflowContext:
    """Central dependency container for playlist workflow operations.

    Aggregates all services needed for playlist synchronization workflows
    including configuration, logging, music service connectors, database
    access, and business logic use cases.
    """

    logger: LoggerProvider
    connectors: ConnectorRegistry
    use_cases: UseCaseProvider
    session_provider: DatabaseSessionProvider
    shared_session: Any = (
        None  # Optional shared session for workflow transaction boundaries
    )

    async def execute_service[TResult](
        self,
        service_fn: Callable[[UnitOfWorkProtocol], Coroutine[Any, Any, TResult]],
    ) -> TResult:
        """Execute an arbitrary async operation with a UnitOfWork.

        General-purpose method for running service calls, repository operations,
        or any async function that needs a UoW — without the caller importing
        infrastructure.

        Args:
            service_fn: Async callable receiving a UoW and returning a result.

        Returns:
            Result from the service function.
        """
        from src.infrastructure.persistence.repositories.factories import (
            get_unit_of_work,
        )

        if self.shared_session is not None:
            uow = get_unit_of_work(self.shared_session)
            return await service_fn(uow)
        else:
            async with self.session_provider.get_session() as session:
                uow = get_unit_of_work(session)
                return await service_fn(uow)

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


def create_workflow_context(
    shared_session: AsyncSession | None = None,
) -> WorkflowContext:
    """Create a complete workflow context with all dependencies configured.

    Factory function that instantiates and wires together all the services
    needed for playlist workflow operations including configuration, logging,
    music service connectors, database access, and business logic.

    Args:
        shared_session: Optional pre-opened database session to share

    Returns:
        Configured workflow context ready for use
    """
    logger = LoggerProviderImpl()
    connectors = ConnectorRegistryImpl()
    session_provider = DatabaseSessionProviderImpl()
    use_cases = UseCaseProviderImpl(shared_session)

    return ConcreteWorkflowContext(
        logger=logger,
        connectors=connectors,
        use_cases=use_cases,
        session_provider=session_provider,
        shared_session=shared_session,
    )
