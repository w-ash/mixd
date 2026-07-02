"""Play-history repository + import/resolve service protocols.

Split from the former monolithic ``interfaces.py``.
"""

from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol, TypedDict
from uuid import UUID

from attrs import define

from src.domain.entities import (
    ConnectorTrackPlay,
    OperationResult,
    TrackPlay,
)
from src.domain.entities.progress import ProgressEmitter

if TYPE_CHECKING:
    from src.domain.repositories.uow import UnitOfWorkProtocol


@define(frozen=True, slots=True)
class LastfmImportParams:
    """Selectors for a Last.fm play import (API-based, checkpoint-bounded).

    ``username`` is the *request* username (CLI affordance); the importer
    resolves the effective account token-first (see the importer's
    ``_resolve_username``) and threads the resolved name through the pipeline.
    """

    username: str | None = None
    from_date: datetime | None = None
    to_date: datetime | None = None
    limit: int | None = None


@define(frozen=True, slots=True)
class SpotifyImportParams:
    """Selectors for a Spotify play import (personal-data file export)."""

    file_path: Path
    batch_size: int | None = None


type PlayImportParams = LastfmImportParams | SpotifyImportParams


type PlaySortBy = Literal[
    "total_plays_desc",
    "last_played_desc",
    "title_asc",
    "random",
    "played_at_desc",
    "first_played_asc",
]


class PlayAggregationResult(TypedDict, total=False):
    """Typed result from play aggregation queries.

    Each key maps track IDs to their aggregated value. All keys are optional
    (total=False) because callers request specific metric subsets.
    """

    total_plays: dict[UUID, int]
    first_played_dates: dict[UUID, datetime | None]
    last_played_dates: dict[UUID, datetime | None]
    period_plays: dict[UUID, int]


class ResolutionMetrics(TypedDict, total=False):
    """Metrics produced by play resolution.

    All keys are optional (total=False) because different resolvers
    (Spotify, Last.fm) emit different subsets of metrics.
    """

    raw_plays: int
    accepted_plays: int
    duration_excluded: int
    incognito_excluded: int
    error_count: int
    resolution_failures: list[dict[str, str]]
    new_tracks_count: int
    updated_tracks_count: int
    unique_tracks_processed: int
    tracks_resolved: int
    fallback_resolved: int
    redirect_resolved: int
    spotify_enhanced_count: int


class PlaysRepositoryProtocol(Protocol):
    """Repository interface for play history operations."""

    def bulk_insert_plays(self, plays: list[TrackPlay]) -> Awaitable[tuple[int, int]]:
        """Bulk insert plays.

        Returns:
            tuple[int, int]: (inserted_count, duplicate_count)
        """
        ...

    def get_recent_plays(
        self, *, user_id: str, limit: int = 100, sort_by: PlaySortBy | None = None
    ) -> Awaitable[list[TrackPlay]]:
        """Get recent plays.

        Args:
            user_id: Owner's user ID.
            limit: Maximum number of plays to return
            sort_by: Optional sorting method
        """
        ...

    def get_play_aggregations(
        self,
        track_ids: list[UUID],
        metrics: list[str],
        *,
        user_id: str,
        period_start: datetime | None = None,
        period_end: datetime | None = None,
    ) -> Awaitable[PlayAggregationResult]:
        """Get aggregated play data for specified tracks and metrics.

        Args:
            track_ids: List of track IDs to get play data for
            metrics: List of metrics to calculate ["total_plays", "last_played_dates", "period_plays"]
            user_id: Owner's user ID.
            period_start: Start date for period-based metrics (optional)
            period_end: End date for period-based metrics (optional)

        Returns:
            Typed dictionary mapping metric names to {track_id: value} dictionaries.
        """
        ...

    def find_plays_in_time_range(
        self,
        track_ids: list[UUID],
        start: datetime,
        end: datetime,
        *,
        user_id: str,
    ) -> Awaitable[list[TrackPlay]]:
        """Find existing plays for given tracks within a time range.

        Used by cross-source deduplication to find candidate matches.
        """
        ...

    def bulk_update_play_source_services(
        self,
        updates: Sequence[tuple[UUID, Mapping[str, object]]],
    ) -> Awaitable[None]:
        """Batch-update cross-source dedup metadata for multiple plays."""
        ...


class ConnectorPlayRepositoryProtocol(Protocol):
    """Repository interface for connector play operations.

    Handles raw play data from external music services before resolution to canonical plays.
    Follows the same clean pattern as other connector repositories with simple resolution tracking.
    """

    def bulk_insert_connector_plays(
        self, connector_plays: list[ConnectorTrackPlay]
    ) -> Awaitable[tuple[int, int]]:
        """Bulk insert connector plays from external API data.

        Args:
            connector_plays: List of ConnectorTrackPlay domain objects from API ingestion

        Returns:
            tuple[int, int]: (inserted_count, duplicate_count)
        """
        ...


class PlayImporterProtocol(Protocol):
    """Protocol for play import services in infrastructure layer.

    Infrastructure classes implement this to provide service-specific play import
    capabilities (Last.fm API, Spotify file export, etc.). Application layer
    orchestrates via PlayImportOrchestrator without knowing the concrete service.
    """

    async def import_plays(
        self,
        uow: UnitOfWorkProtocol,
        params: PlayImportParams,
        *,
        user_id: str | None = None,
        progress_emitter: ProgressEmitter | None = None,
    ) -> tuple[OperationResult, list[ConnectorTrackPlay]]:
        """Import plays and return result with connector plays for resolution.

        Each importer accepts its own params type from the union and raises
        ``TypeError`` on a mismatch (the importer registry is stringly-typed;
        this check restores the type boundary at runtime).
        """
        ...


class PlayResolverProtocol(Protocol):
    """Protocol for play resolution services in infrastructure layer.

    Resolves raw ConnectorTrackPlay objects to canonical TrackPlay objects
    by looking up or creating canonical tracks for each external ID.
    """

    async def resolve_connector_plays(
        self,
        connector_plays: list[ConnectorTrackPlay],
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> tuple[list[TrackPlay], ResolutionMetrics]:
        """Resolve connector plays to canonical track plays.

        Args:
            connector_plays: Raw plays from external service.
            uow: Unit of work for database operations.
            user_id: Authenticated user ID for data scoping.
            progress_callback: Optional progress reporting.

        Returns:
            Tuple of (resolved track plays, resolution metrics).
        """
        ...
