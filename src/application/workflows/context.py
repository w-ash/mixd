"""Dependency injection container for playlist workflow operations.

Manages configuration, logging, music service connectors, database sessions,
and business logic use cases needed for playlist synchronization workflows.
"""

# pyright: reportExplicitAny=false, reportAny=false
# Legitimate Any: connector config dicts, connector instance cache

from collections.abc import Awaitable, Callable
from typing import Any

from attrs import define

from src.config.constants import BusinessLimits
from src.domain.repositories import UnitOfWorkProtocol

# Approved infrastructure bridge: context.py is a DI container (like runner.py and
# prefect.py). Infrastructure imports for session creation and SQLAlchemy AsyncSession
# are intentional wiring — this is the designated integration point for workflow DI.
from src.infrastructure.persistence.database.db_connection import get_session

# Repository factory functions will be imported locally where needed
from .protocols import (
    ConnectorRegistry,
    MetricConfigProvider,
    UseCase,
    UseCaseProvider,
    WorkflowContext,
)


class ConnectorRegistryImpl:
    """Registry for music service API connectors.

    Manages access to connectors for music services like Spotify, Last.fm,
    and MusicBrainz. Automatically discovers available connectors and caches
    instances so repeated calls return the same connector (same httpx pool).
    """

    _connectors: dict[str, Any]  # heterogeneous connector config dicts
    _cache: dict[str, Any]  # heterogeneous connector instances

    def __init__(self):
        """Initialize connector registry and discover available connectors."""
        from src.infrastructure.connectors import discover_connectors

        self._connectors = discover_connectors()
        self._cache = {}

    def get_connector(self, name: str) -> object:
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

    async def aclose(self) -> None:
        """Close all cached connector instances and their httpx connection pools.

        Mirrors the cleanup pattern in DatabaseUnitOfWork.__aexit__ (lines 90-94).
        Must be called when the workflow completes (success or failure) to avoid
        leaking httpx connection pools until GC.
        """
        from src.application.connector_protocols import Closeable

        for connector in self._cache.values():
            if isinstance(connector, Closeable):
                await connector.aclose()
        self._cache.clear()


class UseCaseProviderImpl:
    """Factory for playlist and track management business logic.

    Creates instances of use cases that handle playlist operations like
    creating playlists, matching tracks between services, enriching track
    metadata, and synchronizing playlists across music services.

    Each method returns its concrete type so pyright can propagate result
    types through ``execute_use_case`` generics.
    """

    def __init__(self, metric_config: MetricConfigProvider) -> None:
        self._metric_config = metric_config

    async def get_create_canonical_playlist_use_case(self):
        from src.application.use_cases.create_canonical_playlist import (
            CreateCanonicalPlaylistUseCase,
        )

        return CreateCanonicalPlaylistUseCase(metric_config=self._metric_config)

    async def get_create_connector_playlist_use_case(self):
        from src.application.use_cases.create_connector_playlist import (
            CreateConnectorPlaylistUseCase,
        )

        return CreateConnectorPlaylistUseCase()

    async def get_enrich_tracks_use_case(self):
        from src.application.use_cases.enrich_tracks import EnrichTracksUseCase

        return EnrichTracksUseCase(metric_config=self._metric_config)

    async def get_liked_tracks_use_case(self):
        from src.application.use_cases.get_liked_tracks import (
            GetLikedTracksUseCase,
        )

        return GetLikedTracksUseCase()

    async def get_played_tracks_use_case(self):
        from src.application.use_cases.get_played_tracks import (
            GetPlayedTracksUseCase,
        )

        return GetPlayedTracksUseCase()

    async def get_update_canonical_playlist_use_case(self):
        from src.application.use_cases.update_canonical_playlist import (
            UpdateCanonicalPlaylistUseCase,
        )

        return UpdateCanonicalPlaylistUseCase(metric_config=self._metric_config)

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

    connectors: ConnectorRegistry
    use_cases: UseCaseProvider
    metric_config: MetricConfigProvider
    user_id: str = BusinessLimits.DEFAULT_USER_ID

    async def _with_uow[TResult](
        self,
        fn: Callable[[UnitOfWorkProtocol], Awaitable[TResult]],
    ) -> TResult:
        """Acquire a UnitOfWork and run an async function against it.

        Each call creates a fresh session from the PostgreSQL connection pool.
        Per-task sessions are safe under MVCC — no shared session needed.

        Wraps execution in ``user_context()`` so the ``after_begin`` event
        sets ``SET LOCAL app.user_id`` on the PostgreSQL transaction for RLS.

        Args:
            fn: Async callable receiving a UoW and returning a result.
        """
        from src.infrastructure.persistence.database.user_context import user_context
        from src.infrastructure.persistence.repositories.factories import (
            get_unit_of_work,
        )

        async with get_session() as session:
            with user_context(self.user_id):
                return await fn(get_unit_of_work(session))

    async def execute_service[TResult](
        self,
        service_fn: Callable[[UnitOfWorkProtocol], Awaitable[TResult]],
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
        return await self._with_uow(service_fn)

    async def execute_use_case[TCommand, TResult](
        self,
        use_case_getter: Callable[[], Awaitable[UseCase[TCommand, TResult]]],
        command: TCommand,
    ) -> TResult:
        """Execute a business logic use case with proper resource management.

        Handles database session lifecycle, unit of work creation, and
        cleanup automatically for any workflow use case execution.

        Args:
            use_case_getter: Async function that returns a configured use case.
            command: Command object containing operation parameters.

        Returns:
            Typed result from the executed use case.
        """
        use_case = await use_case_getter()
        return await self._with_uow(lambda uow: use_case.execute(command, uow))


def create_workflow_context(
    user_id: str = BusinessLimits.DEFAULT_USER_ID,
) -> WorkflowContext:
    """Create a complete workflow context with all dependencies configured.

    Factory function that instantiates and wires together all the services
    needed for playlist workflow operations including configuration, logging,
    music service connectors, database access, and business logic.

    Each use case / service call creates its own database session from the
    PostgreSQL connection pool — no shared session needed under MVCC.

    Args:
        user_id: Current user ID for multi-tenant data isolation.  Propagated
            to per-task sessions via ``user_context()`` so RLS and repo-level
            WHERE clauses scope data to this user.

    Returns:
        Configured workflow context ready for use
    """
    from src.infrastructure.connectors._shared.metric_registry import (
        MetricConfigProviderImpl,
    )

    connectors = ConnectorRegistryImpl()
    metric_config = MetricConfigProviderImpl()
    use_cases = UseCaseProviderImpl(metric_config=metric_config)

    return ConcreteWorkflowContext(
        connectors=connectors,
        use_cases=use_cases,
        metric_config=metric_config,
        user_id=user_id,
    )
