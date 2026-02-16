"""Generic play import orchestration service implementing two-phase workflow.

Orchestrates the complete play import process with clean separation of concerns:
1. Phase 1: Raw data ingestion via pluggable importers (connector_plays)
2. Phase 2: Deferred resolution via ConnectorPlayResolutionService (track_plays)

This service contains generic business logic for the two-phase workflow while accepting
pluggable importer instances from the infrastructure layer.
"""

from typing import Any, Protocol

from src.config import get_logger
from src.domain.entities import ConnectorTrackPlay, OperationResult, TrackPlay
from src.domain.entities.progress import NullProgressEmitter, ProgressEmitter
from src.domain.repositories import UnitOfWorkProtocol

logger = get_logger(__name__)


class PlayImporterProtocol(Protocol):
    """Protocol for play import services in infrastructure layer."""

    async def import_plays(
        self, uow: UnitOfWorkProtocol, **params: Any
    ) -> tuple[OperationResult, list[ConnectorTrackPlay]]:
        """Import plays and return result with connector plays for resolution."""
        ...


class PlayImportOrchestrator:
    """Orchestrates two-phase play import workflow with clean architecture separation.

    Accepts pluggable importer instances to avoid mentioning specific connectors.
    All connector-specific logic lives in the infrastructure layer.
    """

    async def import_plays_two_phase(
        self,
        importer: PlayImporterProtocol,
        uow: UnitOfWorkProtocol,
        progress_emitter: ProgressEmitter | None = None,
        **import_params: Any,
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

        # Phase 2: Deferred resolution (track_plays)
        logger.info(f"Phase 2: Resolving {len(connector_plays)} connector plays")
        resolution_result = await self._execute_resolution_phase(connector_plays, uow)

        # Combine results for unified reporting
        combined_result = self._combine_phase_results(
            ingestion_result, resolution_result
        )

        # Extract success rate from combined result summary metrics
        success_rate_metric = next(
            (
                m
                for m in combined_result.summary_metrics.metrics
                if m.name == "success_rate"
            ),
            None,
        )
        success_rate_str = (
            f"{success_rate_metric.value:.1f}%" if success_rate_metric else "N/A"
        )

        # Extract resolved plays from resolution result
        resolved_count = next(
            (
                m.value
                for m in resolution_result.summary_metrics.metrics
                if m.name == "resolved"
            ),
            0,
        )

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
    ) -> OperationResult:
        """Execute Phase 2: Resolve connector_plays to canonical track_plays.

        Uses existing clean resolvers directly - no redundant application service.
        """
        if not connector_plays:
            return self._create_empty_resolution_result()

        # Group connector plays by service for service-specific resolution
        spotify_plays = [p for p in connector_plays if p.connector_name == "spotify"]
        lastfm_plays = [p for p in connector_plays if p.connector_name == "lastfm"]

        all_track_plays = []
        combined_metrics = {
            "total_plays": len(connector_plays),
            "resolved_plays": 0,
            "error_count": 0,
        }

        # Resolve Spotify plays using existing clean resolver
        if spotify_plays:
            (
                spotify_track_plays,
                spotify_metrics,
            ) = await self._resolve_spotify_plays_direct(spotify_plays, uow)
            all_track_plays.extend(spotify_track_plays)
            combined_metrics["resolved_plays"] += len(spotify_track_plays)
            combined_metrics["error_count"] += spotify_metrics.get("error_count", 0)

        # Resolve Last.fm plays using existing clean resolver
        if lastfm_plays:
            (
                lastfm_track_plays,
                lastfm_metrics,
            ) = await self._resolve_lastfm_plays_direct(lastfm_plays, uow)
            all_track_plays.extend(lastfm_track_plays)
            combined_metrics["resolved_plays"] += len(lastfm_track_plays)
            combined_metrics["error_count"] += lastfm_metrics.get("error_count", 0)

        # Save all resolved track_plays to database
        if all_track_plays:
            plays_repo = uow.get_plays_repository()
            await plays_repo.bulk_insert_plays(all_track_plays)
            logger.info(f"Saved {len(all_track_plays)} resolved track plays")

        # Convert to OperationResult with summary metrics
        result = OperationResult(
            operation_name="Connector Play Resolution",
            execution_time=0.0,  # Timing handled at orchestrator level
        )

        # Add metadata
        result.metadata.update(combined_metrics)

        # Add summary metrics
        total_plays = combined_metrics["total_plays"]
        resolved = len(all_track_plays)
        errors = combined_metrics["error_count"]
        filtered = total_plays - resolved - errors

        result.summary_metrics.add("total", total_plays, "Total Plays", significance=0)
        result.summary_metrics.add(
            "resolved", resolved, "Track Plays Resolved", significance=1
        )
        if filtered > 0:
            result.summary_metrics.add("filtered", filtered, "Filtered", significance=2)
        if errors > 0:
            result.summary_metrics.add("errors", errors, "Errors", significance=3)

        return result

    async def _resolve_spotify_plays_direct(
        self,
        spotify_plays: list[ConnectorTrackPlay],
        uow: UnitOfWorkProtocol,
    ) -> tuple[list[TrackPlay], dict[str, Any]]:
        """Resolve Spotify plays using existing clean resolver directly."""
        from src.infrastructure.connectors.spotify.play_resolver import (
            SpotifyConnectorPlayResolver,
        )

        resolver = SpotifyConnectorPlayResolver()
        return await resolver.resolve_connector_plays(spotify_plays, uow)

    async def _resolve_lastfm_plays_direct(
        self,
        lastfm_plays: list[ConnectorTrackPlay],
        uow: UnitOfWorkProtocol,
    ) -> tuple[list[TrackPlay], dict[str, Any]]:
        """Resolve Last.fm plays using existing clean resolver directly."""
        from src.infrastructure.connectors.lastfm.play_resolver import (
            LastfmConnectorPlayResolver,
        )

        resolver = LastfmConnectorPlayResolver()
        return await resolver.resolve_connector_plays(lastfm_plays, uow)

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
        ingestion_imported = next(
            (
                m.value
                for m in ingestion_result.summary_metrics.metrics
                if m.name == "imported"
            ),
            0,
        )
        ingestion_duplicates = next(
            (
                m.value
                for m in ingestion_result.summary_metrics.metrics
                if m.name == "duplicates"
            ),
            0,
        )
        ingestion_errors = next(
            (
                m.value
                for m in ingestion_result.summary_metrics.metrics
                if m.name == "errors"
            ),
            0,
        )
        raw_plays = next(
            (
                m.value
                for m in ingestion_result.summary_metrics.metrics
                if m.name == "raw_plays"
            ),
            0,
        )

        # Extract values from resolution result summary metrics
        resolved = next(
            (
                m.value
                for m in resolution_result.summary_metrics.metrics
                if m.name == "resolved"
            ),
            0,
        )
        resolution_filtered = next(
            (
                m.value
                for m in resolution_result.summary_metrics.metrics
                if m.name == "filtered"
            ),
            0,
        )
        resolution_errors = next(
            (
                m.value
                for m in resolution_result.summary_metrics.metrics
                if m.name == "errors"
            ),
            0,
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

        return result
