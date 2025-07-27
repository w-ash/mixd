"""Track identity resolution use case implementing Clean Architecture patterns.

This module contains the core business logic for resolving track identities across
music services, following Clean Architecture principles:
- Command pattern for rich context encapsulation
- Batch-first design (single items are degenerate cases)
- Use case orchestrates business logic, delegates infrastructure concerns
- Domain entities for input/output, no infrastructure types exposed
"""

from typing import Any

from attrs import define, field

from src.config import get_logger
from src.domain.entities.track import TrackList
from src.domain.matching.types import MatchResultsById
from src.domain.repositories import UnitOfWorkProtocol

logger = get_logger(__name__)


@define(frozen=True, slots=True)
class ResolveTrackIdentityCommand:
    """Command for track identity resolution operations.

    Encapsulates all context needed for resolving track identities across
    music services with proper validation and configuration.
    """

    tracklist: TrackList
    connector: str
    connector_instance: Any
    additional_options: dict[str, Any] = field(factory=dict)

    def __attrs_post_init__(self) -> None:
        """Validate command parameters."""
        if not self.connector:
            raise ValueError("Connector name must be specified")
        if not self.connector_instance:
            raise ValueError("Connector instance must be provided")


@define(frozen=True, slots=True)
class ResolveTrackIdentityResult:
    """Result of track identity resolution operation.

    Contains identity mappings and operation metadata for downstream
    processing and monitoring.
    """

    identity_mappings: MatchResultsById
    track_count: int
    resolved_count: int
    execution_time_ms: int = 0
    errors: list[str] = field(factory=list)


@define(slots=True)
class ResolveTrackIdentityUseCase:
    """Use case for track identity resolution across music services.

    This use case serves as the single source of truth for all track identity
    resolution in the system. It orchestrates the business logic while delegating
    infrastructure concerns to services accessed through UnitOfWork.

    Used by:
    - Enrichment workflows (to establish track identity before fetching metrics)
    - Sync operations (to map tracks between different services)
    - Playlist operations (to resolve track identities for cross-service operations)
    - MatchTracksUseCase (backward compatibility wrapper)

    Migrated to UnitOfWork pattern for pure Clean Architecture compliance:
    - No constructor dependencies (pure domain layer)
    - Explicit transaction control through UnitOfWork parameter
    - Simplified testing with single UnitOfWork mock
    - Consistent pattern with all other use cases
    """

    async def execute(
        self, command: ResolveTrackIdentityCommand, uow: UnitOfWorkProtocol
    ) -> ResolveTrackIdentityResult:
        """Execute track identity resolution operation.

        Args:
            command: Rich command with operation context and configuration.
            uow: UnitOfWork for transaction management and repository access.

        Returns:
            Result containing identity mappings and operation metadata.
        """
        import time

        start_time = time.time()

        with logger.contextualize(
            operation="resolve_track_identity_use_case",
            connector=command.connector,
            track_count=len(command.tracklist.tracks),
        ):
            logger.info(
                f"Starting track identity resolution for {len(command.tracklist.tracks)} tracks"
            )

            # Validate tracks have database IDs (required for identity resolution)
            valid_tracks = [t for t in command.tracklist.tracks if t.id is not None]
            if not valid_tracks:
                logger.warning(
                    "No tracks with database IDs - unable to resolve identities"
                )
                execution_time_ms = int((time.time() - start_time) * 1000)
                return ResolveTrackIdentityResult(
                    identity_mappings={},
                    track_count=len(command.tracklist.tracks),
                    resolved_count=0,
                    execution_time_ms=execution_time_ms,
                    errors=[
                        "No tracks with database IDs available for identity resolution"
                    ],
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
                # Get track identity service from UnitOfWork
                track_identity_service = uow.get_track_identity_service()

                # Delegate to infrastructure service for actual identity resolution
                identity_mappings = (
                    await track_identity_service.resolve_track_identities(
                        filtered_tracklist,
                        command.connector,
                        command.connector_instance,
                        **command.additional_options,
                    )
                )

                execution_time_ms = int((time.time() - start_time) * 1000)
                resolved_count = len(identity_mappings)

                logger.info(
                    f"Successfully resolved {resolved_count} out of {len(valid_tracks)} track identities"
                )

                return ResolveTrackIdentityResult(
                    identity_mappings=identity_mappings,
                    track_count=len(command.tracklist.tracks),
                    resolved_count=resolved_count,
                    execution_time_ms=execution_time_ms,
                    errors=[],
                )

            except Exception as e:
                execution_time_ms = int((time.time() - start_time) * 1000)
                error_msg = f"Track identity resolution failed: {e}"
                logger.error(error_msg)

                return ResolveTrackIdentityResult(
                    identity_mappings={},
                    track_count=len(command.tracklist.tracks),
                    resolved_count=0,
                    execution_time_ms=execution_time_ms,
                    errors=[error_msg],
                )
