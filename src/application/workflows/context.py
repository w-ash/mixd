"""Dependency injection container for playlist workflow operations.

Manages configuration, logging, music service connectors, database sessions,
and business logic use cases needed for playlist synchronization workflows.
"""

from collections.abc import Awaitable, Callable

from attrs import define

from src.application.runner import execute_use_case as run_with_uow
from src.application.use_cases._shared.connector_catalog import (
    ConnectorCatalog,
    default_connector_catalog,
)
from src.application.use_cases._shared.metric_config import (
    MetricConfigProvider,
    default_metric_config,
)
from src.config.constants import BusinessLimits
from src.domain.entities.connector import ConnectorDescriptor
from src.domain.repositories.uow import UnitOfWorkProtocol

from .protocols import (
    ConnectorRegistry,
    UseCase,
    UseCaseProvider,
    WorkflowContext,
)


class ConnectorRegistryImpl:
    """Registry for music service API connectors.

    Reads names and builds instances through the ``ConnectorCatalog`` port, so
    the registry never names infrastructure itself. Instances are cached, so
    repeated calls return the same connector (same httpx2 pool).
    """

    _catalog: ConnectorCatalog
    _cache: dict[str, object]
    _descriptors: dict[str, ConnectorDescriptor]

    def __init__(self, catalog: ConnectorCatalog | None = None) -> None:
        """Initialize the registry against a connector catalog.

        Args:
            catalog: Catalog to read connectors from. Defaults to the
                discovery-backed catalog.
        """
        self._catalog = default_connector_catalog() if catalog is None else catalog
        self._cache = {}
        self._descriptors = {}

    def describe(self, name: str) -> ConnectorDescriptor:
        """Return the static descriptor for one connector, cached per name.

        The catalog rebuilds every descriptor on each call, so the first
        lookup per registry lifetime pays that cost and later ones do not.

        Raises:
            ValueError: If connector name is not registered
        """
        descriptor = self._descriptors.get(name)
        if descriptor is None:
            descriptor = self._catalog.describe(name)
            self._descriptors[name] = descriptor
        return descriptor

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

        instance = self._catalog.create_connector(name)
        self._cache[name] = instance
        return instance

    def list_connectors(self) -> list[str]:
        """List names of all available music service connectors.

        Returns:
            List of connector names
        """
        return [descriptor.name for descriptor in self._catalog.list_descriptors()]

    async def aclose(self) -> None:
        """Close all cached connector instances and their httpx2 connection pools.

        Mirrors the cleanup pattern in DatabaseUnitOfWork.__aexit__ (lines 90-94).
        Must be called when the workflow completes (success or failure) to avoid
        leaking httpx2 connection pools until GC.
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

    async def get_preferred_tracks_use_case(self):
        from src.application.use_cases.get_preferred_tracks import (
            GetPreferredTracksUseCase,
        )

        return GetPreferredTracksUseCase()

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

    async def execute_service[TResult](
        self,
        service_fn: Callable[[UnitOfWorkProtocol], Awaitable[TResult]],
    ) -> TResult:
        """Execute an arbitrary async operation with a UnitOfWork.

        General-purpose method for running service calls, repository operations,
        or any async function that needs a UoW — without the caller importing
        infrastructure. Delegates to the shared runner, so each call creates a
        fresh session from the PostgreSQL connection pool (safe under MVCC) and
        scopes it to ``user_id`` for RLS.

        Args:
            service_fn: Async callable receiving a UoW and returning a result.

        Returns:
            Result from the service function.
        """
        return await run_with_uow(service_fn, user_id=self.user_id)

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
        return await self.execute_service(lambda uow: use_case.execute(command, uow))


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
    connectors = ConnectorRegistryImpl()
    metric_config = default_metric_config()
    use_cases = UseCaseProviderImpl(metric_config=metric_config)

    return ConcreteWorkflowContext(
        connectors=connectors,
        use_cases=use_cases,
        metric_config=metric_config,
        user_id=user_id,
    )
