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
        **import_params: Any,
    ) -> OperationResult:
        """Execute two-phase play import: ingestion then resolution.

        Args:
            importer: Pluggable importer instance from infrastructure layer
            uow: Unit of work for database operations
            **import_params: Importer-specific parameters

        Returns:
            Combined operation result with ingestion and resolution metrics
        """
        logger.info("Starting two-phase play import")

        # Phase 1: Raw data ingestion (connector_plays)
        logger.info("Phase 1: Ingesting raw play data")
        ingestion_result, connector_plays = await importer.import_plays(
            uow, **import_params
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

        logger.info(
            "Two-phase import complete",
            ingested_plays=len(connector_plays),
            resolved_plays=resolution_result.imported_count,
            success_rate=f"{combined_result.success_rate:.1f}%",
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

        # Convert to OperationResult
        return OperationResult(
            operation_name="Connector Play Resolution",
            imported_count=len(all_track_plays),
            filtered_count=combined_metrics["total_plays"]
            - combined_metrics["resolved_plays"]
            - combined_metrics["error_count"],
            duplicate_count=0,  # No duplicates in resolution phase
            error_count=combined_metrics["error_count"],
            execution_time=0.0,  # Timing handled at orchestrator level
            play_metrics=combined_metrics,
        )

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
        return OperationResult(
            operation_name="Connector Play Resolution",
            imported_count=0,
            filtered_count=0,
            duplicate_count=0,
            error_count=0,
            execution_time=0.0,
            play_metrics={"total_plays": 0, "resolved_plays": 0, "error_count": 0},
        )

    def _combine_phase_results(
        self,
        ingestion_result: OperationResult,
        resolution_result: OperationResult,
    ) -> OperationResult:
        """Combine ingestion and resolution results into unified report.

        Provides user with comprehensive view of the two-phase workflow.
        """
        return OperationResult(
            operation_name="Two-Phase Play Import",
            plays_processed=ingestion_result.plays_processed,  # Raw tracks that entered the system
            imported_count=resolution_result.imported_count,  # Final canonical plays
            filtered_count=resolution_result.filtered_count,
            duplicate_count=ingestion_result.duplicate_count,  # From ingestion phase
            error_count=(ingestion_result.error_count or 0)
            + (resolution_result.error_count or 0),
            execution_time=(ingestion_result.execution_time or 0)
            + (resolution_result.execution_time or 0),
            play_metrics={
                "ingestion_phase": {
                    "connector_plays_ingested": ingestion_result.imported_count,
                    "ingestion_errors": ingestion_result.error_count,
                    "ingestion_duplicates": ingestion_result.duplicate_count,
                },
                "resolution_phase": {
                    "track_plays_resolved": resolution_result.imported_count,
                    "resolution_errors": resolution_result.error_count,
                    "plays_filtered": resolution_result.filtered_count,
                },
                "combined_metrics": resolution_result.play_metrics,
            },
        )
