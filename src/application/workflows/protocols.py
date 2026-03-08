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

# pyright: reportExplicitAny=false, reportAny=false
# Legitimate Any: RunStatusUpdater **kwargs pass-through

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, NotRequired, Protocol, TypedDict

from src.application.use_cases.create_canonical_playlist import (
    CreateCanonicalPlaylistUseCase,
)
from src.application.use_cases.create_connector_playlist import (
    CreateConnectorPlaylistUseCase,
)
from src.application.use_cases.enrich_tracks import EnrichTracksUseCase
from src.application.use_cases.get_liked_tracks import GetLikedTracksUseCase
from src.application.use_cases.get_played_tracks import GetPlayedTracksUseCase
from src.application.use_cases.read_canonical_playlist import (
    ReadCanonicalPlaylistUseCase,
)
from src.application.use_cases.update_canonical_playlist import (
    UpdateCanonicalPlaylistUseCase,
)
from src.application.use_cases.update_connector_playlist import (
    UpdateConnectorPlaylistUseCase,
)
from src.domain.entities.track import TrackList
from src.domain.entities.workflow import NodeExecutionEvent, RunStatus, TrackDecision
from src.domain.repositories import UnitOfWorkProtocol


class UseCase[TCommand, TResult](Protocol):
    """Protocol for use cases invoked by workflow nodes.

    All workflow-facing use cases accept a command and a UnitOfWork,
    returning a typed result. This enables generic dispatch in
    ``execute_use_case`` so pyright can infer concrete result types.
    """

    async def execute(self, command: TCommand, uow: UnitOfWorkProtocol) -> TResult: ...


class ConnectorRegistry(Protocol):
    """Dynamic access to multiple music service connectors (Spotify, Last.fm, MusicBrainz)."""

    def get_connector(self, name: str) -> object:
        """Get specific music service connector.

        Returns object; callers narrow via capability protocols
        (PlaylistConnector, LikedTrackConnector, etc.).
        """
        ...

    def list_connectors(self) -> list[str]:
        """Get names of all available connectors."""
        ...

    async def aclose(self) -> None:
        """Close all cached connector instances and their connection pools."""
        ...


class UseCaseProvider(Protocol):
    """Provides business logic use cases with dependency injection.

    Each method returns its concrete use case type, enabling pyright to
    infer result types through ``execute_use_case``'s generic signature.
    """

    async def get_create_canonical_playlist_use_case(
        self,
    ) -> CreateCanonicalPlaylistUseCase:
        """Get use case for creating master playlist definitions."""
        ...

    async def get_create_connector_playlist_use_case(
        self,
    ) -> CreateConnectorPlaylistUseCase:
        """Get use case for creating service-specific playlists."""
        ...

    async def get_enrich_tracks_use_case(self) -> EnrichTracksUseCase:
        """Get use case for enriching tracks with cross-service metadata."""
        ...

    async def get_liked_tracks_use_case(self) -> GetLikedTracksUseCase:
        """Get use case for retrieving user's liked/favorited tracks."""
        ...

    async def get_played_tracks_use_case(self) -> GetPlayedTracksUseCase:
        """Get use case for retrieving user's listening history."""
        ...

    async def get_read_canonical_playlist_use_case(
        self,
    ) -> ReadCanonicalPlaylistUseCase:
        """Get use case for reading master playlist definitions."""
        ...

    async def get_update_canonical_playlist_use_case(
        self,
    ) -> UpdateCanonicalPlaylistUseCase:
        """Get use case for updating master playlist definitions."""
        ...

    async def get_update_connector_playlist_use_case(
        self,
    ) -> UpdateConnectorPlaylistUseCase:
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

    async def execute_service[TResult](
        self,
        service_fn: Callable[[UnitOfWorkProtocol], Awaitable[TResult]],
        /,
    ) -> TResult:
        """Execute an async operation with a UnitOfWork.

        General-purpose method for service calls or any async function
        that needs a UoW without the caller importing infrastructure.

        Args:
            service_fn: Async callable receiving a UoW and returning a result.

        Returns:
            Result from the service function.
        """
        ...

    async def execute_use_case[TCommand, TResult](
        self,
        use_case_getter: Callable[[], Awaitable[UseCase[TCommand, TResult]]],
        command: TCommand,
    ) -> TResult:
        """Execute business logic with automatic transaction management.

        The generic signature allows pyright to infer the concrete result
        type from the use case getter — e.g. passing ``get_enrich_tracks_use_case``
        lets callers access ``result.enriched_tracklist`` without casts.

        Args:
            use_case_getter: Async function that returns a configured use case.
            command: Command object containing operation parameters.

        Returns:
            Typed result from the executed use case.
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


class RunStatusUpdater(Protocol):
    """Typed contract for run-level status updates.

    Concrete impls live in the interface layer, injected at the call site.
    """

    async def __call__(self, run_id: int, status: RunStatus, **kwargs: Any) -> None: ...


class NodeStatusUpdater(Protocol):
    """Typed contract for node-level status updates.

    Concrete impls live in the interface layer, injected at the call site.
    """

    async def __call__(
        self,
        run_id: int,
        node_id: str,
        status: RunStatus,
        *,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
        duration_ms: int | None = None,
        input_track_count: int | None = None,
        output_track_count: int | None = None,
        error_message: str | None = None,
        node_details: dict[str, Any] | None = None,
    ) -> None: ...


class NodeResult(TypedDict):
    """Minimum contract for all workflow node outputs.

    Every node (source, transform, destination) returns at least a tracklist.
    Destination nodes also return operation-specific keys; structural typing
    allows those extra keys while enforcing the tracklist contract.
    ``track_decisions`` is optional — only transform/destination nodes produce it.
    """

    tracklist: TrackList
    track_decisions: NotRequired[list[TrackDecision]]


class NodeExecutionObserver(Protocol):
    """Callback protocol for node lifecycle events during workflow execution.

    Implementations receive notifications when nodes start, complete, or fail.
    This is THE hook point for v0.4.1: web SSE events, run-history recording,
    and CLI progress all implement this same protocol.

    Note: Sub-operation progress within nodes (e.g., "Fetching page 3 from Spotify")
    uses a separate mechanism (emit_phase_progress). This observer tracks node-level
    lifecycle only.
    """

    async def on_node_starting(self, event: NodeExecutionEvent) -> None: ...

    async def on_node_completed(
        self, event: NodeExecutionEvent, result: NodeResult
    ) -> None: ...

    async def on_node_failed(
        self, event: NodeExecutionEvent, error: Exception
    ) -> None: ...
