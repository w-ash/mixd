"""Generic play import orchestration service implementing two-phase workflow.

Orchestrates the complete play import process with clean separation of concerns:
1. Phase 1: Raw data ingestion via pluggable importers (connector_plays)
2. Phase 2: Deferred resolution via ConnectorPlayResolutionService (track_plays)

This service contains generic business logic for the two-phase workflow while accepting
pluggable importer instances from the infrastructure layer.
"""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from uuid import UUID

from attrs import define

from src.application.services.play_projection_service import (
    PROJECTION_FETCH_MARGIN,
    PROJECTION_STAT_LABELS,
    PlayProjectionService,
)
from src.application.use_cases._shared.batch_commit import commit_batch
from src.config import get_logger
from src.domain.entities import ConnectorTrackPlay, OperationResult, TrackPlay
from src.domain.entities.progress import (
    NullProgressEmitter,
    ProgressEmitter,
    create_progress_event,
    tracked_operation,
)
from src.domain.repositories.play import (
    PlayImporterProtocol,
    PlayImportParams,
    PlayResolverProtocol,
)
from src.domain.repositories.uow import UnitOfWorkProtocol

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
        params: PlayImportParams,
        progress_emitter: ProgressEmitter | None = None,
    ) -> OperationResult:
        """Execute two-phase play import: ingestion then resolution.

        Args:
            importer: Pluggable importer instance from infrastructure layer
            uow: Unit of work for database operations
            user_id: The mixd user id, threaded to the importer for token-first
                account resolution (the cross-tenant leak fix)
            params: Importer-specific frozen import selectors
            progress_emitter: Optional progress emitter (defaults to null implementation)

        Returns:
            Combined operation result with ingestion and resolution metrics
        """
        if progress_emitter is None:
            progress_emitter = NullProgressEmitter()

        logger.info("Starting two-phase play import")

        # Phase 1: Raw data ingestion (connector_plays)
        logger.info("Phase 1: Ingesting raw play data")
        ingestion_result, connector_plays = await importer.import_plays(
            uow, params, user_id=user_id, progress_emitter=progress_emitter
        )

        if not connector_plays:
            logger.info("No plays to resolve - ingestion phase complete")
            return ingestion_result

        # Commit Phase 1 data so it survives if Phase 2 crashes
        await commit_batch(uow)

        # Phase 2: Deferred resolution (track_plays)
        logger.info(f"Phase 2: Resolving {len(connector_plays)} connector plays")
        resolution_result = await self.execute_resolution_phase(
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

    async def execute_resolution_phase(
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
        all_resolutions: list[tuple[ConnectorTrackPlay, UUID]] = []
        combined_metrics = {
            "total_plays": len(connector_plays),
            "resolved_plays": 0,
            "error_count": 0,
            "fallback_resolved": 0,
            "redirect_resolved": 0,
            "dead_ids_unresolved": 0,
            "isrc_suspect_deferred": 0,
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
                    outcome = await resolver.resolve_connector_plays(
                        plays, uow, user_id=user_id
                    )
                    track_plays, metrics = outcome.track_plays, outcome.metrics
                    all_track_plays.extend(track_plays)
                    all_resolutions.extend(outcome.resolutions)
                    combined_metrics["resolved_plays"] += len(track_plays)
                    combined_metrics["error_count"] += metrics.get("error_count", 0)
                    combined_metrics["fallback_resolved"] += metrics.get(
                        "fallback_resolved", 0
                    )
                    combined_metrics["redirect_resolved"] += metrics.get(
                        "redirect_resolved", 0
                    )
                    combined_metrics["dead_ids_unresolved"] += metrics.get(
                        "dead_ids_unresolved", 0
                    )
                    combined_metrics["isrc_suspect_deferred"] += metrics.get(
                        "isrc_suspect_deferred", 0
                    )

                    await progress_emitter.emit_progress(
                        create_progress_event(
                            operation_id=operation_id,
                            current=len(all_track_plays),
                            total=len(connector_plays),
                            message=f"Resolved {len(all_track_plays)}/{len(connector_plays)} plays ({service})",
                        )
                    )

            # Phase 3: ledger write-back, then project the affected window.
            # Canonical plays are derived from the observation ledger — the
            # resolver-built TrackPlays above only feed metrics.
            projection_stats: dict[str, int] = {}
            if all_resolutions:
                async with uow:
                    _ = await uow.get_connector_play_repository().bulk_update_resolution(
                        all_resolutions, resolved_at=datetime.now(UTC)
                    )
                    await uow.commit()

                played = [cp.played_at for cp, _ in all_resolutions]
                projection = PlayProjectionService()
                # Re-enter the UoW context so an exception mid-chunk rolls
                # back the pending writes — ImportTracksUseCase converts
                # exceptions into a failed result, and without this bracket
                # the runner's session teardown would COMMIT the half-applied
                # chunk instead (per-chunk commit_batch durability is
                # unaffected; only the failure path changes).
                async with uow:
                    projection_stats = await projection.project_range(
                        uow,
                        user_id=user_id,
                        start=min(played) - PROJECTION_FETCH_MARGIN,
                        end=max(played) + PROJECTION_FETCH_MARGIN,
                        progress_emitter=progress_emitter,
                        operation_id=operation_id,
                    )
                logger.info(
                    "Projected batch window onto canonical plays",
                    **projection_stats,
                )

        # Convert to OperationResult with summary metrics
        result = OperationResult(
            operation_name="Connector Play Resolution",
            execution_time=0.0,  # Timing handled at orchestrator level
        )

        # Add metadata
        result.metadata.update(combined_metrics)
        if projection_stats:
            result.metadata["play_projection"] = projection_stats

        # Add summary metrics
        total_plays = combined_metrics["total_plays"]
        resolved = len(all_track_plays)
        errors = combined_metrics["error_count"]
        filtered = total_plays - resolved - errors

        result.summary_metrics.add("total", total_plays, "Total Plays", significance=0)
        result.summary_metrics.add(
            "resolved", resolved, "Track Plays Resolved", significance=1
        )
        for stat_key in (
            "groups_created",
            "groups_updated",
            "groups_merged",
            "resolution_divergence",
        ):
            if projection_stats.get(stat_key, 0) > 0:
                result.summary_metrics.add(
                    stat_key,
                    projection_stats[stat_key],
                    PROJECTION_STAT_LABELS[stat_key],
                    significance=2,
                )
        if filtered > 0:
            result.summary_metrics.add("filtered", filtered, "Filtered", significance=3)
        if errors > 0:
            result.summary_metrics.add("errors", errors, "Errors", significance=4)

        fallback_resolved = combined_metrics["fallback_resolved"]
        redirect_resolved = combined_metrics["redirect_resolved"]
        dead_ids_unresolved = combined_metrics["dead_ids_unresolved"]
        isrc_suspect_deferred = combined_metrics["isrc_suspect_deferred"]

        if fallback_resolved > 0:
            result.summary_metrics.add(
                "fallback_resolved",
                fallback_resolved,
                "Resolved via Search Fallback",
                significance=5,
            )
        if redirect_resolved > 0:
            result.summary_metrics.add(
                "redirect_resolved",
                redirect_resolved,
                "Resolved via Spotify Redirect",
                significance=6,
            )
        if dead_ids_unresolved > 0:
            result.summary_metrics.add(
                "dead_ids_unresolved",
                dead_ids_unresolved,
                "Dead IDs Unresolved",
                significance=7,
            )
        if isrc_suspect_deferred > 0:
            result.summary_metrics.add(
                "isrc_suspect_deferred",
                isrc_suspect_deferred,
                "ISRC Suspect — Deferred to Review",
                significance=8,
            )

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
        fallback_resolved = resolution_result.summary_metrics.get("fallback_resolved")
        redirect_resolved = resolution_result.summary_metrics.get("redirect_resolved")
        dead_ids_unresolved = resolution_result.summary_metrics.get(
            "dead_ids_unresolved"
        )
        isrc_suspect_deferred = resolution_result.summary_metrics.get(
            "isrc_suspect_deferred"
        )

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

        if fallback_resolved > 0:
            result.summary_metrics.add(
                "fallback_resolved",
                int(fallback_resolved),
                "Resolved via Search Fallback",
                significance=7,
            )
        if redirect_resolved > 0:
            result.summary_metrics.add(
                "redirect_resolved",
                int(redirect_resolved),
                "Resolved via Spotify Redirect",
                significance=8,
            )
        if dead_ids_unresolved > 0:
            result.summary_metrics.add(
                "dead_ids_unresolved",
                int(dead_ids_unresolved),
                "Dead IDs Unresolved",
                significance=9,
            )
        if isrc_suspect_deferred > 0:
            result.summary_metrics.add(
                "isrc_suspect_deferred",
                int(isrc_suspect_deferred),
                "ISRC Suspect — Deferred to Review",
                significance=10,
            )

        return result
