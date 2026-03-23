"""Match and identify tracks using the new unambiguous identity pipeline.

This use case serves as the **SOLE ORCHESTRATOR** for Identity Resolution in the system.
It coordinates the complete track identity resolution workflow while delegating all
business logic to the domain layer and all infrastructure concerns to the infrastructure layer.

This replaces MatchTracksUseCase and will become the single way to resolve track identities.
"""

# pyright: reportExplicitAny=false, reportAny=false
# Legitimate Any: use case results, OperationResult metadata, metric values

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.application.services.progress_manager import AsyncProgressManager

from attrs import define, field

from src.application.utilities.timing import ExecutionTimer
from src.config import create_evaluation_service, get_logger
from src.config.logging import logging_context
from src.domain.entities.match_review import MatchReview
from src.domain.entities.track import Track, TrackList
from src.domain.matching.evaluation_service import TrackMatchEvaluationService
from src.domain.matching.types import MatchResultsById, RawProviderMatch
from src.domain.repositories import UnitOfWorkProtocol
from src.domain.repositories.interfaces import TrackIdentityServiceProtocol

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
        progress_manager: Optional progress manager for sub-operation tracking.
        parent_operation_id: Parent operation ID for sub-operation nesting.
    """

    tracklist: TrackList
    connector: str
    connector_instance: Any  # ServiceConnectorProvider.get_connector() returns Any
    max_age_hours: float | None = None
    additional_options: dict[str, Any] = field(factory=dict)
    progress_manager: AsyncProgressManager | None = None
    parent_operation_id: str | None = None

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
    _evaluation_service: TrackMatchEvaluationService = field(init=False)

    def __attrs_post_init__(self) -> None:
        self._evaluation_service = create_evaluation_service()

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
        timer = ExecutionTimer()

        # Note: TrackMappingService initialization removed - relinking handled by SpotifyConnector

        with logging_context(
            operation="match_and_identify_tracks",
            connector=command.connector,
            track_count=len(command.tracklist.tracks),
        ):
            logger.info(
                f"Starting track identity resolution for {len(command.tracklist.tracks)} tracks"
            )

            # Business rule: empty tracklist is valid, return early
            if not command.tracklist.tracks:
                return MatchAndIdentifyTracksResult(
                    identity_mappings={},
                    track_count=0,
                    resolved_count=0,
                    execution_time_ms=timer.stop(),
                    errors=[],
                )

            # Business rule: only process tracks with database IDs
            valid_tracks = [t for t in command.tracklist.tracks if t.id is not None]
            if not valid_tracks:
                logger.warning(
                    "No tracks with database IDs - unable to perform identity resolution"
                )
                return MatchAndIdentifyTracksResult(
                    identity_mappings={},
                    track_count=len(command.tracklist.tracks),
                    resolved_count=0,
                    execution_time_ms=timer.stop(),
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
                    raw_matches = await self._fetch_raw_matches_with_progress(
                        track_identity_service=track_identity_service,
                        tracks=tracks_needing_resolution,
                        command=command,
                    )

                    # STEP 3.5: NOTE: Spotify relinking handling removed - now automatic
                    # SpotifyConnector already maps both old/new IDs to same data
                    # MatchAndIdentifyTracksUseCase naturally creates one canonical track

                    # STEP 4: Apply ALL business logic through domain service
                    evaluation = self._evaluation_service.evaluate_raw_matches(
                        tracks=tracks_needing_resolution,
                        raw_matches=raw_matches,
                        connector=command.connector,
                    )

                    # STEP 5: Persist auto-accepted identity mappings
                    if evaluation.accepted:
                        await track_identity_service.persist_identity_mappings(
                            evaluation.accepted, command.connector
                        )
                        logger.info(
                            f"Persisted {len(evaluation.accepted)} new identity mappings"
                        )

                    # STEP 5b: Persist review candidates to match_reviews table
                    if evaluation.review_candidates:
                        await self._persist_review_candidates(
                            evaluation.review_candidates, command.connector, uow
                        )
                        logger.info(
                            f"{len(evaluation.review_candidates)} matches queued for review"
                        )

                    # Combine existing and newly accepted mappings
                    identity_mappings = {**existing_mappings, **evaluation.accepted}
                else:
                    logger.info("All tracks already have identity mappings")
                    identity_mappings = existing_mappings

                resolved_count = len(identity_mappings)

                logger.info(
                    f"Successfully resolved {resolved_count} out of {len(valid_tracks)} track identities"
                )

                return MatchAndIdentifyTracksResult(
                    identity_mappings=identity_mappings,
                    track_count=len(command.tracklist.tracks),
                    resolved_count=resolved_count,
                    execution_time_ms=timer.stop(),
                    errors=[],
                )

            except Exception as e:
                error_msg = f"Track identity resolution failed: {e}"
                logger.error(error_msg)

                return MatchAndIdentifyTracksResult(
                    identity_mappings={},
                    track_count=len(command.tracklist.tracks),
                    resolved_count=0,
                    execution_time_ms=timer.stop(),
                    errors=[error_msg],
                )

    async def _fetch_raw_matches_with_progress(
        self,
        track_identity_service: TrackIdentityServiceProtocol,
        tracks: list[Track],
        command: MatchAndIdentifyTracksCommand,
    ) -> dict[int, RawProviderMatch]:
        """Fetch raw matches with optional progress sub-operation tracking.

        Creates a sub-operation on the progress manager when available, threads
        the callback to the matching provider, and ensures proper completion/failure
        status reporting.

        Args:
            track_identity_service: The TrackIdentityServiceProtocol instance from UoW.
            tracks: Tracks needing identity resolution.
            command: The command containing connector and progress configuration.

        Returns:
            Raw matches dict from the provider.
        """
        from src.application.services.sub_operation_progress import (
            complete_sub_operation,
            create_sub_operation,
        )
        from src.domain.entities.progress import OperationStatus

        progress_callback = None
        sub_op_id: str | None = None
        try:
            if command.progress_manager and command.parent_operation_id:
                sub_op_id, progress_callback = await create_sub_operation(
                    command.progress_manager,
                    description=f"Matching tracks to {command.connector}",
                    total_items=len(tracks),
                    parent_operation_id=command.parent_operation_id,
                    phase="match",
                    node_type="enricher",
                )

            raw_matches = await track_identity_service.get_raw_external_matches(
                tracks,
                command.connector,
                command.connector_instance,
                progress_callback=progress_callback,
                **command.additional_options,
            )

            if command.progress_manager and sub_op_id:
                await complete_sub_operation(command.progress_manager, sub_op_id)
        except Exception:
            if command.progress_manager and sub_op_id:
                await complete_sub_operation(
                    command.progress_manager, sub_op_id, OperationStatus.FAILED
                )
            raise
        else:
            return raw_matches

    async def _persist_review_candidates(
        self,
        review_candidates: MatchResultsById,
        connector: str,
        uow: UnitOfWorkProtocol,
    ) -> None:
        """Persist review-zone matches to the match_reviews table."""
        review_repo = uow.get_match_review_repository()
        reviews = [
            MatchReview(
                track_id=track_id,
                connector_name=connector,
                connector_track_id=int(match.connector_id) if match.connector_id else 0,
                match_method=match.match_method,
                confidence=match.confidence,
                match_weight=match.evidence.match_weight if match.evidence else 0.0,
                confidence_evidence=match.evidence_dict,
            )
            for track_id, match in review_candidates.items()
            if match.connector_id
        ]
        if reviews:
            await review_repo.create_reviews_batch(reviews)

    # NOTE: _handle_spotify_relinking method removed in Phase 4
    # REASON: SpotifyConnector.get_tracks_by_ids() already handles relinking transparently
    # - Maps both old and new IDs to identical track data
    # - MatchAndIdentifyTracksUseCase naturally creates one canonical track
    # - No additional business logic needed for relinking
    # EVIDENCE: Real API testing showed 11/21 tracks (52%) were relinked successfully
    # RESULT: TrackMappingService and relinking orchestration proven redundant
