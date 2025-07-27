"""Track enrichment use case implementing Clean Architecture patterns.

This module contains the core business logic for enriching tracks with metrics
from both external services (Spotify, LastFM) and internal data (play history).
Follows Clean Architecture principles with ruthlessly DRY implementation:
- Single use case for all enrichment types (batch-first design)
- Uses TrackIdentityUseCase for identity resolution
- Command pattern for rich context encapsulation
- Strategy pattern for different enrichment types
"""

from typing import Any, Literal

from attrs import define, field

from src.config import get_logger
from src.domain.entities.track import TrackList
from src.domain.repositories import UnitOfWorkProtocol

logger = get_logger(__name__)

# Type definitions for enrichment configuration
EnrichmentType = Literal["external_metadata", "play_history"]
ConnectorType = Literal["spotify", "lastfm", "musicbrainz"]


@define(frozen=True, slots=True)
class EnrichmentConfig:
    """Configuration for track enrichment operations.

    Unified configuration supporting both external metadata and internal
    play history enrichment with proper validation and defaults.
    """

    enrichment_type: EnrichmentType

    # External metadata enrichment options
    connector: ConnectorType | None = None
    connector_instance: Any = None
    extractors: dict[str, Any] = field(factory=dict)
    max_age_hours: float | None = None

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
            if not self.extractors:
                raise ValueError(
                    "Extractors must be specified for external metadata enrichment"
                )
        elif self.enrichment_type == "play_history":
            if not self.metrics:
                raise ValueError(
                    "Metrics must be specified for play history enrichment"
                )


@define(frozen=True, slots=True)
class EnrichTracksCommand:
    """Command for track enrichment operations.

    Encapsulates all context needed for enriching tracks with metrics
    from various sources (external services or internal data).
    """

    tracklist: TrackList
    enrichment_config: EnrichmentConfig

    def __attrs_post_init__(self) -> None:
        """Validate command parameters."""
        # Allow empty tracklists - the use case will handle this gracefully


@define(frozen=True, slots=True)
class EnrichTracksResult:
    """Result of track enrichment operation.

    Contains enriched tracklist with metrics and operation metadata
    for monitoring and downstream processing.
    """

    enriched_tracklist: TrackList
    metrics_added: dict[str, dict[int, Any]]
    track_count: int
    enriched_count: int
    execution_time_ms: int = 0
    errors: list[str] = field(factory=list)


@define(slots=True)
class EnrichTracksUseCase:
    """Use case for track enrichment with metrics from various sources.

    This use case serves as the single source of truth for all track enrichment
    in the system, handling both external metadata (LastFM, Spotify) and internal
    data (play history) through a unified interface.

    Architectural principles:
    - Ruthlessly DRY: Single use case for all enrichment types
    - Batch-first: Designed for N tracks, single track is degenerate case
    - Strategy pattern: Different enrichment strategies based on type

    Used by:
    - Workflow enricher nodes (external metadata enrichment)
    - Play history enrichment workflows (internal data enrichment)
    - Any operation requiring track metrics for sorting/filtering

    Migrated to UnitOfWork pattern for pure Clean Architecture compliance:
    - No constructor dependencies (pure domain layer)
    - Explicit transaction control through UnitOfWork parameter
    - Simplified testing with single UnitOfWork mock
    - Consistent pattern with all other use cases
    """

    async def execute(
        self, command: EnrichTracksCommand, uow: UnitOfWorkProtocol
    ) -> EnrichTracksResult:
        """Execute track enrichment operation.

        Args:
            command: Rich command with operation context and configuration.
            uow: UnitOfWork for transaction management and repository access.

        Returns:
            Result containing enriched tracklist and operation metadata.
        """
        import time

        start_time = time.time()

        with logger.contextualize(
            operation="enrich_tracks_use_case",
            enrichment_type=command.enrichment_config.enrichment_type,
            track_count=len(command.tracklist.tracks),
        ):
            logger.info(
                f"Starting {command.enrichment_config.enrichment_type} enrichment "
                f"for {len(command.tracklist.tracks)} tracks"
            )

            # Validate tracks have database IDs (required for enrichment)
            valid_tracks = [t for t in command.tracklist.tracks if t.id is not None]
            if not valid_tracks:
                logger.warning("No tracks with database IDs - unable to enrich")
                execution_time_ms = int((time.time() - start_time) * 1000)
                return EnrichTracksResult(
                    enriched_tracklist=command.tracklist,
                    metrics_added={},
                    track_count=len(command.tracklist.tracks),
                    enriched_count=0,
                    execution_time_ms=execution_time_ms,
                    errors=["No tracks with database IDs available for enrichment"],
                )

            # Filter out tracks without IDs and log the discrepancy
            filtered_count = len(command.tracklist.tracks) - len(valid_tracks)
            if filtered_count > 0:
                logger.info(
                    f"Filtered out {filtered_count} tracks without database IDs"
                )

            # Create filtered tracklist for processing
            filtered_tracklist = TrackList(
                tracks=valid_tracks, metadata=command.tracklist.metadata
            )

            try:
                # Delegate to appropriate enrichment strategy
                if command.enrichment_config.enrichment_type == "external_metadata":
                    result = await self._enrich_external_metadata(
                        filtered_tracklist, command.enrichment_config, uow
                    )
                elif command.enrichment_config.enrichment_type == "play_history":
                    result = await self._enrich_play_history(
                        filtered_tracklist, command.enrichment_config, uow
                    )
                else:
                    raise ValueError(
                        f"Unknown enrichment type: {command.enrichment_config.enrichment_type}"
                    )

                execution_time_ms = int((time.time() - start_time) * 1000)
                enriched_count = sum(len(metrics) for metrics in result[1].values())

                logger.info(
                    f"Successfully enriched tracks with {enriched_count} total metric values"
                )

                return EnrichTracksResult(
                    enriched_tracklist=result[0],
                    metrics_added=result[1],
                    track_count=len(command.tracklist.tracks),
                    enriched_count=enriched_count,
                    execution_time_ms=execution_time_ms,
                    errors=[],
                )

            except Exception as e:
                execution_time_ms = int((time.time() - start_time) * 1000)
                error_msg = f"Track enrichment failed: {e}"
                logger.error(error_msg)

                return EnrichTracksResult(
                    enriched_tracklist=command.tracklist,
                    metrics_added={},
                    track_count=len(command.tracklist.tracks),
                    enriched_count=0,
                    execution_time_ms=execution_time_ms,
                    errors=[error_msg],
                )

    async def _enrich_external_metadata(
        self, tracklist: TrackList, config: EnrichmentConfig, uow: UnitOfWorkProtocol
    ) -> tuple[TrackList, dict[str, dict[int, Any]]]:
        """Enrich tracks with external service metadata.

        Uses the external metadata service from UoW to fetch and extract
        metrics from external services through proper Clean Architecture boundaries.

        Args:
            tracklist: Tracks to enrich (must have database IDs).
            config: External metadata enrichment configuration.
            uow: UnitOfWork for accessing external metadata service.

        Returns:
            Tuple of (enriched_tracklist, metrics_dictionary).
        """
        logger.info(f"Enriching with {config.connector} metadata")

        # Get external metadata service from UnitOfWork
        # Note: config.connector is guaranteed to be non-None by EnrichmentConfig validation
        if config.connector is None:
            raise ValueError(
                "Connector must be specified for external metadata enrichment"
            )

        external_metadata_service = uow.get_external_metadata_service()
        (
            enriched_tracklist,
            metrics,
        ) = await external_metadata_service.fetch_and_extract_metadata(
            tracklist,
            config.connector,
            config.connector_instance,
            config.extractors,
            config.max_age_hours,
            **config.additional_options,
        )

        return enriched_tracklist, metrics

    async def _enrich_play_history(
        self, tracklist: TrackList, config: EnrichmentConfig, uow: UnitOfWorkProtocol
    ) -> tuple[TrackList, dict[str, dict[int, Any]]]:
        """Enrich tracks with internal play history data.

        Directly accesses play history data from the database without
        requiring identity resolution (internal data).

        Args:
            tracklist: Tracks to enrich (must have database IDs).
            config: Play history enrichment configuration.
            uow: UnitOfWork for accessing plays repository.

        Returns:
            Tuple of (enriched_tracklist, metrics_dictionary).
        """
        from datetime import UTC, datetime, timedelta

        logger.info(f"Enriching with play history metrics: {config.metrics}")

        if not tracklist.tracks:
            logger.info("No tracks to enrich")
            return tracklist, {}

        # Extract valid track IDs
        valid_tracks = [t for t in tracklist.tracks if t.id is not None]
        if not valid_tracks:
            logger.warning(
                "No tracks have database IDs - unable to enrich play history"
            )
            return tracklist, {}

        track_ids = [t.id for t in valid_tracks if t.id is not None]

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

        return enriched_tracklist, play_metrics
