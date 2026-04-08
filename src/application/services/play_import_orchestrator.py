"""Generic play import orchestration service implementing two-phase workflow.

Orchestrates the complete play import process with clean separation of concerns:
1. Phase 1: Raw data ingestion via pluggable importers (connector_plays)
2. Phase 2: Deferred resolution via ConnectorPlayResolutionService (track_plays)

This service contains generic business logic for the two-phase workflow while accepting
pluggable importer instances from the infrastructure layer.
"""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from attrs import define

from src.application.use_cases._shared.batch_commit import commit_batch
from src.config import get_logger
from src.domain.entities import ConnectorTrackPlay, OperationResult, TrackPlay
from src.domain.entities.progress import (
    NullProgressEmitter,
    ProgressEmitter,
    create_progress_event,
    tracked_operation,
)
from src.domain.matching.play_dedup import (
    compute_dedup_time_range,
    deduplicate_cross_source_plays,
)
from src.domain.repositories import (
    PlayImporterProtocol,
    PlayResolverProtocol,
    UnitOfWorkProtocol,
)

logger = get_logger(__name__)


@define(slots=True)
class PlayImportOrchestrator:
    """Orchestrates two-phase play import workflow with clean architecture separation.

    Accepts pluggable importer instances to avoid mentioning specific connectors.
    All connector-specific logic lives in the infrastructure layer.
    """

    resolver_factory: Callable[[str], Awaitable[PlayResolverProtocol]]

    async def import_plays_two_phase(
        self,
        importer: PlayImporterProtocol,
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
        progress_emitter: ProgressEmitter | None = None,
        **import_params: object,
    ) -> OperationResult:
        """Execute two-phase play import: ingestion then resolution.

        Args:
            importer: Pluggable importer instance from infrastructure layer
            uow: Unit of work for database operations
            progress_emitter: Optional progress emitter (defaults to null implementation)
            **import_params: Importer-specific parameters

        Returns:
            Combined operation result with ingestion and resolution metrics
        """
        if progress_emitter is None:
            progress_emitter = NullProgressEmitter()

        logger.info("Starting two-phase play import")

        # Phase 1: Raw data ingestion (connector_plays)
        logger.info("Phase 1: Ingesting raw play data")
        ingestion_result, connector_plays = await importer.import_plays(
            uow, progress_emitter=progress_emitter, **import_params
        )

        if not connector_plays:
            logger.info("No plays to resolve - ingestion phase complete")
            return ingestion_result

        # Commit Phase 1 data so it survives if Phase 2 crashes
        await commit_batch(uow)

        # Phase 2: Deferred resolution (track_plays)
        logger.info(f"Phase 2: Resolving {len(connector_plays)} connector plays")
        resolution_result = await self._execute_resolution_phase(
            connector_plays, uow, user_id=user_id, progress_emitter=progress_emitter
        )

        # Combine results for unified reporting
        combined_result = self._combine_phase_results(
            ingestion_result, resolution_result
        )

        # Extract success rate from combined result summary metrics
        success_rate = combined_result.summary_metrics.get("success_rate")
        success_rate_str = f"{success_rate:.1f}%" if success_rate else "N/A"

        # Extract resolved plays from resolution result
        resolved_count = resolution_result.summary_metrics.get("resolved")

        logger.info(
            "Two-phase import complete",
            ingested_plays=len(connector_plays),
            resolved_plays=int(resolved_count),
            success_rate=success_rate_str,
        )

        return combined_result

    async def _execute_resolution_phase(
        self,
        connector_plays: list[ConnectorTrackPlay],
        uow: UnitOfWorkProtocol,
        *,
        user_id: str,
        progress_emitter: ProgressEmitter,
    ) -> OperationResult:
        """Execute Phase 2: Resolve connector_plays to canonical track_plays.

        Uses existing clean resolvers directly - no redundant application service.
        """
        if not connector_plays:
            return self._create_empty_resolution_result()

        # Group connector plays by service for service-specific resolution
        spotify_plays = [p for p in connector_plays if p.connector_name == "spotify"]
        lastfm_plays = [p for p in connector_plays if p.connector_name == "lastfm"]

        all_track_plays: list[TrackPlay] = []
        combined_metrics = {
            "total_plays": len(connector_plays),
            "resolved_plays": 0,
            "error_count": 0,
        }

        async with tracked_operation(
            progress_emitter, "Resolving plays to canonical tracks"
        ) as operation_id:
            # Resolve plays per service using registry-provided resolvers
            for service, plays in [
                ("spotify", spotify_plays),
                ("lastfm", lastfm_plays),
            ]:
                if plays:
                    resolver = await self.resolver_factory(service)
                    track_plays, metrics = await resolver.resolve_connector_plays(
                        plays, uow, user_id=user_id
                    )
                    all_track_plays.extend(track_plays)
                    combined_metrics["resolved_plays"] += len(track_plays)
                    combined_metrics["error_count"] += metrics.get("error_count", 0)

                    await progress_emitter.emit_progress(
                        create_progress_event(
                            operation_id=operation_id,
                            current=len(all_track_plays),
                            total=len(connector_plays),
                            message=f"Resolved {len(all_track_plays)}/{len(connector_plays)} plays ({service})",
                        )
                    )

            # Cross-source dedup then save to database
            dedup_stats: dict[str, int] = {}
            if all_track_plays:
                async with uow:
                    plays_repo = uow.get_plays_repository()

                    # Query existing plays in the time range for dedup comparison
                    time_range = compute_dedup_time_range(all_track_plays)
                    if time_range is not None:
                        start_epoch, end_epoch = time_range
                        track_ids = list({
                            p.track_id
                            for p in all_track_plays
                            if p.track_id is not None
                        })
                        start_dt = datetime.fromtimestamp(start_epoch, tz=UTC)
                        end_dt = datetime.fromtimestamp(end_epoch, tz=UTC)
                        existing_plays = await plays_repo.find_plays_in_time_range(
                            track_ids, start_dt, end_dt, user_id=user_id
                        )
                    else:
                        existing_plays = []

                    # Run cross-source dedup
                    dedup_result = deduplicate_cross_source_plays(
                        new_plays=all_track_plays, existing_plays=existing_plays
                    )
                    dedup_stats = dedup_result.stats

                    if dedup_result.stats.get("cross_source_matches", 0) > 0:
                        logger.info(
                            "Cross-source dedup matched plays",
                            matches=dedup_result.stats.get("cross_source_matches", 0),
                            suppressed=len(dedup_result.suppressed_plays),
                        )

                    # Batch-update existing plays enriched by cross-source match
                    if dedup_result.plays_to_update:
                        await plays_repo.bulk_update_play_source_services(
                            dedup_result.plays_to_update
                        )

                    # Insert only truly new plays (after dedup)
                    if dedup_result.plays_to_insert:
                        _ = await plays_repo.bulk_insert_plays(
                            dedup_result.plays_to_insert
                        )

                    await uow.commit()
                    logger.info(
                        f"Saved {len(dedup_result.plays_to_insert)} plays "
                        f"({len(dedup_result.suppressed_plays)} suppressed by cross-source dedup)"
                    )

        # Convert to OperationResult with summary metrics
        result = OperationResult(
            operation_name="Connector Play Resolution",
            execution_time=0.0,  # Timing handled at orchestrator level
        )

        # Add metadata
        result.metadata.update(combined_metrics)
        if dedup_stats:
            result.metadata["cross_source_dedup"] = dedup_stats

        # Add summary metrics
        total_plays = combined_metrics["total_plays"]
        resolved = len(all_track_plays)
        errors = combined_metrics["error_count"]
        filtered = total_plays - resolved - errors
        cross_source_matches = dedup_stats.get("cross_source_matches", 0)

        result.summary_metrics.add("total", total_plays, "Total Plays", significance=0)
        result.summary_metrics.add(
            "resolved", resolved, "Track Plays Resolved", significance=1
        )
        if cross_source_matches > 0:
            result.summary_metrics.add(
                "cross_source_dedup",
                cross_source_matches,
                "Cross-Source Dedup",
                significance=2,
            )
        if filtered > 0:
            result.summary_metrics.add("filtered", filtered, "Filtered", significance=3)
        if errors > 0:
            result.summary_metrics.add("errors", errors, "Errors", significance=4)

        return result

    def _create_empty_resolution_result(self) -> OperationResult:
        """Create empty resolution result when no plays to resolve."""
        result = OperationResult(
            operation_name="Connector Play Resolution",
            execution_time=0.0,
        )

        # Add metadata
        result.metadata.update({
            "total_plays": 0,
            "resolved_plays": 0,
            "error_count": 0,
        })

        # Add summary metrics showing zeros
        result.summary_metrics.add("total", 0, "Total Plays", significance=0)
        result.summary_metrics.add(
            "resolved", 0, "Track Plays Resolved", significance=1
        )

        return result

    def _combine_phase_results(
        self,
        ingestion_result: OperationResult,
        resolution_result: OperationResult,
    ) -> OperationResult:
        """Combine ingestion and resolution results into unified report.

        Provides user with comprehensive view of the two-phase workflow.
        """
        result = OperationResult(
            operation_name="Two-Phase Play Import",
            execution_time=ingestion_result.execution_time
            + resolution_result.execution_time,
        )

        # Combine metadata from both phases
        result.metadata["ingestion_phase"] = {
            "batch_id": ingestion_result.metadata.get("batch_id"),
            "checkpoint_timestamp": ingestion_result.metadata.get(
                "checkpoint_timestamp"
            ),
        }
        result.metadata["resolution_phase"] = resolution_result.metadata.copy()

        # Extract values from ingestion result summary metrics
        ingestion_imported = ingestion_result.summary_metrics.get("imported")
        ingestion_duplicates = ingestion_result.summary_metrics.get("duplicates")
        ingestion_errors = ingestion_result.summary_metrics.get("errors")
        raw_plays = ingestion_result.summary_metrics.get("raw_plays")

        # Extract values from resolution result summary metrics
        resolved = resolution_result.summary_metrics.get("resolved")
        resolution_filtered = resolution_result.summary_metrics.get("filtered")
        resolution_errors = resolution_result.summary_metrics.get("errors")

        total_errors = int(ingestion_errors + resolution_errors)

        # Add combined summary metrics
        result.summary_metrics.add(
            "raw_plays", int(raw_plays), "Raw Plays Found", significance=0
        )
        result.summary_metrics.add(
            "connector_plays",
            int(ingestion_imported),
            "Connector Plays Ingested",
            significance=1,
        )
        result.summary_metrics.add(
            "track_plays", int(resolved), "Track Plays Created", significance=2
        )

        if ingestion_duplicates > 0:
            result.summary_metrics.add(
                "duplicates",
                int(ingestion_duplicates),
                "Filtered (Duplicates)",
                significance=3,
            )
        if resolution_filtered > 0:
            result.summary_metrics.add(
                "filtered", int(resolution_filtered), "Filtered", significance=4
            )
        if total_errors > 0:
            result.summary_metrics.add("errors", total_errors, "Errors", significance=5)

        # Calculate success rate
        attempted = int(resolved + resolution_filtered + resolution_errors)
        if attempted > 0:
            success_rate = (resolved / attempted) * 100
            result.summary_metrics.add(
                "success_rate",
                success_rate,
                "Success Rate",
                format="percent",
                significance=6,
            )

        return result
