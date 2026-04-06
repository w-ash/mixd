"""Enriches tracks with metadata from external APIs and internal play history.

Supports two types of enrichment:
- External metadata: Fetch track details from Spotify, LastFM, MusicBrainz
- Play history: Add play counts, last played dates from database

Processes multiple tracks efficiently in batches.
"""

# pyright: reportExplicitAny=false
# Legitimate Any: use case results, OperationResult metadata, metric values

from typing import TYPE_CHECKING, Any, Literal, Never, cast
from uuid import UUID

if TYPE_CHECKING:
    from src.application.services.progress_manager import AsyncProgressManager
    from src.application.workflows.protocols import MetricConfigProvider

from attrs import define, field

from src.application.connector_protocols import TrackMetadataConnector
from src.application.services.metrics_application_service import (
    MetricsApplicationService,
)
from src.application.utilities.timing import ExecutionTimer
from src.config import get_logger
from src.config.logging import logging_context
from src.domain.entities.track import TrackList
from src.domain.repositories import UnitOfWorkProtocol

logger = get_logger(__name__)

# Type definitions for enrichment configuration
EnrichmentType = Literal["external_metadata", "play_history"]
ConnectorType = Literal["spotify", "lastfm", "musicbrainz"]


@define(frozen=True, slots=True)
class EnrichmentConfig:
    """Configuration for track enrichment operations.

    Specifies enrichment type (external metadata or play history) and
    required parameters for each type. Validates configuration on creation.
    """

    enrichment_type: EnrichmentType

    # External metadata enrichment options
    connector: ConnectorType | None = None
    connector_instance: TrackMetadataConnector | None = None
    track_metric_names: list[str] = field(factory=list)

    # Play history enrichment options
    metrics: list[str] = field(factory=lambda: ["total_plays", "last_played_dates"])
    period_days: int | None = None

    # Common options
    additional_options: dict[str, Any] = field(factory=dict)

    def __attrs_post_init__(self) -> None:
        """Validate enrichment configuration."""
        if self.enrichment_type == "external_metadata":
            if not self.connector:
                raise ValueError(
                    "Connector must be specified for external metadata enrichment"
                )
            if not self.connector_instance:
                raise ValueError(
                    "Connector instance must be provided for external metadata enrichment"
                )
            if not self.track_metric_names:
                raise ValueError(
                    "Track metric names must be specified for external metadata enrichment"
                )
        elif self.enrichment_type == "play_history":
            if not self.metrics:
                raise ValueError(
                    "Metrics must be specified for play history enrichment"
                )


@define(frozen=True, slots=True)
class EnrichTracksCommand:
    """Command for track enrichment operations.

    Contains tracks to enrich and configuration specifying the enrichment
    type and parameters. Allows empty tracklists.
    """

    user_id: str
    tracklist: TrackList
    enrichment_config: EnrichmentConfig
    progress_manager: AsyncProgressManager | None = None
    parent_operation_id: str | None = None

    def __attrs_post_init__(self) -> None:
        """Validate command parameters."""
        # Allow empty tracklists - the use case will handle this gracefully


@define(frozen=True, slots=True)
class EnrichTracksResult:
    """Result of track enrichment operation.

    Contains enriched tracks with added metadata, operation statistics
    (counts, timing), and any errors encountered during processing.
    """

    enriched_tracklist: TrackList
    metrics_added: dict[str, dict[UUID, Any]]
    track_count: int
    enriched_count: int
    execution_time_ms: int = 0
    errors: list[str] = field(factory=list)


@define(slots=True)
class EnrichTracksUseCase:
    """Enriches tracks with metadata from external APIs or internal play data.

    Supports two enrichment types:
    - External metadata: Fetches track details from Spotify, LastFM, MusicBrainz
    - Play history: Adds play counts and timestamps from database

    Processes multiple tracks in batches for efficiency. Filters out tracks
    without database IDs as they cannot be enriched. Returns detailed results
    including operation statistics and any errors.

    Used by workflow enrichment nodes and play history workflows.
    """

    metric_config: MetricConfigProvider
    metrics_service: MetricsApplicationService = field(init=False)

    def __attrs_post_init__(self) -> None:
        self.metrics_service = MetricsApplicationService(
            metric_config=self.metric_config
        )

    async def execute(
        self, command: EnrichTracksCommand, uow: UnitOfWorkProtocol
    ) -> EnrichTracksResult:
        """Enriches tracks with metadata based on configuration.

        Filters tracks to those with database IDs, then delegates to either
        external metadata or play history enrichment based on config type.
        Returns detailed results including operation statistics.

        Args:
            command: Enrichment command with tracks and configuration.
            uow: Unit of work for repository and service access.

        Returns:
            Result with enriched tracks, metrics added, and operation stats.
        """
        timer = ExecutionTimer()

        async with uow:
            with logging_context(
                operation="enrich_tracks_use_case",
                enrichment_type=command.enrichment_config.enrichment_type,
                track_count=len(command.tracklist.tracks),
            ):
                logger.info(
                    f"Starting {command.enrichment_config.enrichment_type} enrichment "
                    + f"for {len(command.tracklist.tracks)} tracks"
                )

                if not command.tracklist.tracks:
                    logger.info("Empty tracklist — nothing to enrich")
                    return EnrichTracksResult(
                        enriched_tracklist=command.tracklist,
                        metrics_added={},
                        track_count=0,
                        enriched_count=0,
                        execution_time_ms=timer.stop(),
                    )

                def _raise_unknown_enrichment_type_error(enrichment_type: str) -> Never:
                    raise ValueError(f"Unknown enrichment type: {enrichment_type}")

                try:
                    # Delegate to appropriate enrichment strategy
                    if command.enrichment_config.enrichment_type == "external_metadata":
                        result = await self._enrich_external_metadata(
                            command.tracklist,
                            command.enrichment_config,
                            uow,
                            user_id=command.user_id,
                            progress_manager=command.progress_manager,
                            parent_operation_id=command.parent_operation_id,
                        )
                    elif command.enrichment_config.enrichment_type == "play_history":
                        result = await self._enrich_play_history(
                            command.tracklist,
                            command.enrichment_config,
                            uow,
                            user_id=command.user_id,
                        )
                    else:
                        _raise_unknown_enrichment_type_error(
                            command.enrichment_config.enrichment_type
                        )

                    enriched_count = sum(len(metrics) for metrics in result[1].values())

                    logger.info(
                        f"Successfully enriched tracks with {enriched_count} total metric values"
                    )

                    return EnrichTracksResult(
                        enriched_tracklist=result[0],
                        metrics_added=result[1],
                        track_count=len(command.tracklist.tracks),
                        enriched_count=enriched_count,
                        execution_time_ms=timer.stop(),
                        errors=[],
                    )

                except Exception as e:
                    error_msg = f"Track enrichment failed: {e}"
                    logger.error(error_msg)

                    return EnrichTracksResult(
                        enriched_tracklist=command.tracklist,
                        metrics_added={},
                        track_count=len(command.tracklist.tracks),
                        enriched_count=0,
                        execution_time_ms=timer.stop(),
                        errors=[error_msg],
                    )

    async def _enrich_external_metadata(
        self,
        tracklist: TrackList,
        config: EnrichmentConfig,
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
        progress_manager: AsyncProgressManager | None = None,
        parent_operation_id: str | None = None,
    ) -> tuple[TrackList, dict[str, dict[UUID, Any]]]:
        """Enriches tracks with metadata from external APIs.

        Fetches track metrics from Spotify, Last.fm, or MusicBrainz using cache-first
        strategy with registered metric configurations. Requires tracks to have database IDs.

        Args:
            tracklist: Tracks to enrich (must have database IDs).
            config: External metadata configuration with connector and extractors.
            uow: Unit of work for database transaction management.

        Returns:
            Tuple of (enriched_tracklist, metrics_dictionary).
        """
        logger.info(f"Enriching with {config.connector} metadata")

        # Validate required configuration
        if config.connector is None:
            raise ValueError(
                "Connector must be specified for external metadata enrichment"
            )
        if config.connector_instance is None:
            raise ValueError(
                "Connector instance must be provided for external metadata enrichment"
            )

        # Get track metric names directly from configuration
        metric_names = config.track_metric_names

        if not metric_names:
            logger.warning("No metrics specified for enrichment")
            return tracklist, {}

        track_ids = [t.id for t in tracklist.tracks]

        logger.info(
            f"Fetching {len(metric_names)} metrics for {len(track_ids)} tracks from {config.connector}"
        )

        # Step 1: Ensure tracks have connector mappings for metric collection
        await self._ensure_track_identities(
            tracklist=tracklist,
            connector=config.connector,
            connector_instance=config.connector_instance,
            uow=uow,
            user_id=user_id,
            progress_manager=progress_manager,
            parent_operation_id=parent_operation_id,
        )

        # Step 2: Use MetricsApplicationService for cache-first metric resolution
        metrics, fresh_ids = await self.metrics_service.get_external_track_metrics(
            track_ids=track_ids,
            connector=config.connector,
            metric_names=metric_names,
            uow=uow,
            connector_instance=config.connector_instance,
            progress_manager=progress_manager,
            parent_operation_id=parent_operation_id,
        )

        # Merge metrics with any existing ones (don't overwrite previous enrichers)
        current_metrics = tracklist.metadata.get("metrics", {})
        merged_metrics = {**current_metrics, **metrics}
        current_fresh = tracklist.metadata.get("fresh_metric_ids", {})
        merged_fresh = {**current_fresh, **{k: list(v) for k, v in fresh_ids.items()}}
        enriched_tracklist = tracklist.with_metadata(
            "metrics", merged_metrics
        ).with_metadata("fresh_metric_ids", merged_fresh)

        logger.info(
            f"Successfully enriched tracklist with {len(metrics)} metric types and "
            + f"{sum(len(values) for values in metrics.values())} total values "
            + f"({sum(len(ids) for ids in fresh_ids.values())} fresh)"
        )

        return enriched_tracklist, metrics

    async def _enrich_play_history(
        self,
        tracklist: TrackList,
        config: EnrichmentConfig,
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
    ) -> tuple[TrackList, dict[str, dict[UUID, Any]]]:
        """Enriches tracks with play history data from database.

        Adds play counts, last played dates, and optionally period-specific
        play counts. Calculates date ranges if period_days is specified.

        Args:
            tracklist: Tracks to enrich (must have database IDs).
            config: Play history configuration with metrics and optional period.
            uow: Unit of work for accessing plays repository.

        Returns:
            Tuple of (enriched_tracklist, metrics_dictionary).
        """
        from datetime import UTC, datetime, timedelta

        logger.info(f"Enriching with play history metrics: {config.metrics}")

        if not tracklist.tracks:
            logger.info("No tracks to enrich")
            return tracklist, {}

        track_ids = [t.id for t in tracklist.tracks]

        # Calculate period boundaries if needed
        period_start, period_end = None, None
        if "period_plays" in config.metrics and config.period_days:
            period_end = datetime.now(UTC)
            period_start = period_end - timedelta(days=config.period_days)

        # Get plays repository from UnitOfWork
        play_repo = uow.get_plays_repository()

        play_metrics = await play_repo.get_play_aggregations(
            track_ids=track_ids,
            metrics=config.metrics,
            user_id=user_id,
            period_start=period_start,
            period_end=period_end,
        )

        if not play_metrics:
            logger.info("No play data found for tracks")
            return tracklist, {}

        # Merge with existing metrics
        current_metrics = tracklist.metadata.get("metrics", {})
        combined_metrics = {**current_metrics, **play_metrics}

        logger.info(f"Enriched with {len(play_metrics)} play metric types")
        enriched_tracklist = tracklist.with_metadata("metrics", combined_metrics)

        # Widen TypedDict → plain dict to match return type shared with external metadata path
        return enriched_tracklist, cast(dict[str, dict[UUID, Any]], play_metrics)

    async def _ensure_track_identities(
        self,
        tracklist: TrackList,
        connector: str,
        connector_instance: TrackMetadataConnector,
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
        progress_manager: AsyncProgressManager | None = None,
        parent_operation_id: str | None = None,
    ) -> None:
        """Ensure tracks have connector mappings by coordinating with the identity resolution system.

        This method serves as a coordinator between enrichment and identity resolution,
        delegating the actual matching work to the existing MatchAndIdentifyTracksUseCase.

        Args:
            tracklist: Tracks that may need identity mappings.
            connector: External service name (e.g., "lastfm").
            connector_instance: Connector instance for API calls.
            uow: Unit of work for database access.
            progress_manager: Optional progress manager for sub-operation tracking.
            parent_operation_id: Parent operation ID for sub-operation nesting.
        """
        from src.application.use_cases.match_and_identify_tracks import (
            MatchAndIdentifyTracksCommand,
            MatchAndIdentifyTracksUseCase,
        )

        logger.info(
            f"Ensuring track identities for {len(tracklist.tracks)} tracks with {connector}",
            connector=connector,
            track_count=len(tracklist.tracks),
        )

        try:
            # Use the existing identity resolution system
            match_command = MatchAndIdentifyTracksCommand(
                user_id=user_id,
                tracklist=tracklist,
                connector=connector,
                connector_instance=connector_instance,
                progress_manager=progress_manager,
                parent_operation_id=parent_operation_id,
            )

            match_use_case = MatchAndIdentifyTracksUseCase()
            match_result = await match_use_case.execute(match_command, uow)

            logger.info(
                f"Identity resolution completed: {match_result.resolved_count}/{match_result.track_count} tracks have {connector} mappings",
                connector=connector,
                resolved_count=match_result.resolved_count,
                total_tracks=match_result.track_count,
            )

            if match_result.errors:
                logger.warning(
                    f"Identity resolution encountered {len(match_result.errors)} errors for {connector}"
                )

        except Exception as e:
            logger.warning(
                f"Failed to ensure track identities for {connector}: {e}",
                connector=connector,
                error=str(e),
            )
            # Don't raise - metrics collection can proceed with existing mappings
