"""Match and identify tracks using the new unambiguous identity pipeline.

This use case serves as the **SOLE ORCHESTRATOR** for Identity Resolution in the system.
It coordinates the complete track identity resolution workflow while delegating all
business logic to the domain layer and all infrastructure concerns to the infrastructure layer.

This replaces MatchTracksUseCase and will become the single way to resolve track identities.
"""

import time
from typing import Any

from attrs import define, field

from src.config import get_logger
from src.domain.entities.track import TrackList
from src.domain.matching.evaluation_service import TrackMatchEvaluationService
from src.domain.matching.types import MatchResultsById
from src.domain.repositories import UnitOfWorkProtocol

# Note: TrackMappingService and spotify_track_lookup removed - redundant with SpotifyConnector behavior

logger = get_logger(__name__)


@define(frozen=True, slots=True)
class MatchAndIdentifyTracksCommand:
    """Input parameters for track identity resolution operation.

    Args:
        tracklist: Tracks to resolve identities for.
        connector: Name of the music service (e.g., "spotify", "lastfm").
        connector_instance: API client instance for the service.
        max_age_hours: Cache expiration time. None means use any cached data.
        additional_options: Service-specific configuration parameters.
    """

    tracklist: TrackList
    connector: str
    connector_instance: Any
    max_age_hours: float | None = None
    additional_options: dict[str, Any] = field(factory=dict)

    def __attrs_post_init__(self) -> None:
        """Validates required parameters are provided."""
        if not self.tracklist:
            raise ValueError("TrackList cannot be None")
        if not self.tracklist.tracks:
            # Empty tracklist is valid - return early in use case
            pass
        if not self.connector:
            raise ValueError("Connector name must be specified")
        if not self.connector_instance:
            raise ValueError("Connector instance must be provided")


@define(frozen=True, slots=True)
class MatchAndIdentifyTracksResult:
    """Results from track identity resolution operation.

    Attributes:
        identity_mappings: Map of track database ID to match results.
        track_count: Total number of tracks processed.
        resolved_count: Number of tracks successfully matched and identified.
        execution_time_ms: Time taken to complete the operation.
        errors: List of error messages if any tracks failed to resolve.
    """

    identity_mappings: MatchResultsById
    track_count: int
    resolved_count: int
    execution_time_ms: int = 0
    errors: list[str] = field(factory=list)


@define(slots=True)
class MatchAndIdentifyTracksUseCase:
    """The SOLE ORCHESTRATOR for Identity Resolution in the system.

    This use case implements the new unambiguous identity pipeline by:
    1. Fetching existing track mappings from database
    2. Getting raw matches from infrastructure providers (no business logic)
    3. Delegating ALL business decisions to domain TrackMatchEvaluationService
    4. Persisting successful identity mappings back to database
    5. Controlling transaction boundaries based on business outcomes

    Key Principles:
    - **Single Responsibility**: ONLY handles Identity Resolution workflow
    - **Domain Delegation**: ALL business logic goes to TrackMatchEvaluationService
    - **Infrastructure Coordination**: Orchestrates raw data providers without decisions
    - **Transaction Control**: Manages database persistence and rollback decisions
    - **Clean Architecture**: Follows dependency flow Infrastructure → Application → Domain

    This replaces:
    - MatchTracksUseCase (will be deleted in Phase 2)
    - All scattered identity resolution logic in import services
    - Direct domain service usage in infrastructure components
    """

    # Domain services injected as class attributes - pure business logic delegation
    _evaluation_service: TrackMatchEvaluationService = field(
        init=False, factory=TrackMatchEvaluationService
    )

    # Note: TrackMappingService removed - SpotifyConnector already handles relinking transparently

    async def execute(
        self, command: MatchAndIdentifyTracksCommand, uow: UnitOfWorkProtocol
    ) -> MatchAndIdentifyTracksResult:
        """Orchestrates track identity resolution with unambiguous pipeline.

        Args:
            command: Parameters including tracks, service connector, and options.
            uow: Unit of work providing access to repositories and services.

        Returns:
            Comprehensive resolution results with success counts, timing, and errors.

        Raises:
            ValueError: Invalid business inputs.
            Exception: Unrecoverable infrastructure errors.
        """
        start_time = time.time()

        # Note: TrackMappingService initialization removed - relinking handled by SpotifyConnector

        with logger.contextualize(
            operation="match_and_identify_tracks",
            connector=command.connector,
            track_count=len(command.tracklist.tracks),
        ):
            logger.info(
                f"Starting track identity resolution for {len(command.tracklist.tracks)} tracks"
            )

            # Business rule: empty tracklist is valid, return early
            if not command.tracklist.tracks:
                execution_time_ms = int((time.time() - start_time) * 1000)
                return MatchAndIdentifyTracksResult(
                    identity_mappings={},
                    track_count=0,
                    resolved_count=0,
                    execution_time_ms=execution_time_ms,
                    errors=[],
                )

            # Business rule: only process tracks with database IDs
            valid_tracks = [t for t in command.tracklist.tracks if t.id is not None]
            if not valid_tracks:
                logger.warning(
                    "No tracks with database IDs - unable to perform identity resolution"
                )
                execution_time_ms = int((time.time() - start_time) * 1000)
                return MatchAndIdentifyTracksResult(
                    identity_mappings={},
                    track_count=len(command.tracklist.tracks),
                    resolved_count=0,
                    execution_time_ms=execution_time_ms,
                    errors=["No tracks with database IDs available for resolution"],
                )

            # Log filtering if needed
            filtered_count = len(command.tracklist.tracks) - len(valid_tracks)
            if filtered_count > 0:
                logger.info(
                    f"Filtered out {filtered_count} tracks without database IDs"
                )

            try:
                # STEP 1: Get existing identity mappings from database
                track_identity_service = uow.get_track_identity_service()
                track_ids = [t.id for t in valid_tracks if t.id is not None]

                existing_mappings = (
                    await track_identity_service.get_existing_identity_mappings(
                        track_ids, command.connector
                    )
                )

                # STEP 2: Find tracks that need new identity resolution
                tracks_needing_resolution = [
                    t for t in valid_tracks if t.id not in existing_mappings
                ]

                if tracks_needing_resolution:
                    logger.info(
                        f"Need to resolve {len(tracks_needing_resolution)} new track identities"
                    )

                    # STEP 3: Get raw matches from infrastructure (no business logic)
                    raw_matches = await track_identity_service.get_raw_external_matches(
                        tracks_needing_resolution,
                        command.connector,
                        command.connector_instance,
                        **command.additional_options,
                    )

                    # STEP 3.5: NOTE: Spotify relinking handling removed - now automatic
                    # SpotifyConnector already maps both old/new IDs to same data
                    # MatchAndIdentifyTracksUseCase naturally creates one canonical track

                    # STEP 4: Apply ALL business logic through domain service
                    new_identity_mappings = (
                        self._evaluation_service.evaluate_raw_matches(
                            tracks=tracks_needing_resolution,
                            raw_matches=raw_matches,
                            connector=command.connector,
                        )
                    )

                    # STEP 5: Persist successful identity mappings (application responsibility)
                    if new_identity_mappings:
                        await track_identity_service.persist_identity_mappings(
                            new_identity_mappings, command.connector
                        )
                        logger.info(
                            f"Persisted {len(new_identity_mappings)} new identity mappings"
                        )

                    # Combine existing and new mappings
                    identity_mappings = {**existing_mappings, **new_identity_mappings}
                else:
                    logger.info("All tracks already have identity mappings")
                    identity_mappings = existing_mappings

                execution_time_ms = int((time.time() - start_time) * 1000)
                resolved_count = len(identity_mappings)

                logger.info(
                    f"Successfully resolved {resolved_count} out of {len(valid_tracks)} track identities"
                )

                return MatchAndIdentifyTracksResult(
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

                return MatchAndIdentifyTracksResult(
                    identity_mappings={},
                    track_count=len(command.tracklist.tracks),
                    resolved_count=0,
                    execution_time_ms=execution_time_ms,
                    errors=[error_msg],
                )

    # NOTE: _handle_spotify_relinking method removed in Phase 4
    # REASON: SpotifyConnector.get_tracks_by_ids() already handles relinking transparently
    # - Maps both old and new IDs to identical track data
    # - MatchAndIdentifyTracksUseCase naturally creates one canonical track
    # - No additional business logic needed for relinking
    # EVIDENCE: Real API testing showed 11/21 tracks (52%) were relinked successfully
    # RESULT: TrackMappingService and relinking orchestration proven redundant
