"""GetLikedTracksUseCase for retrieving liked tracks from canonical database.

This use case handles reading liked tracks from the database following Clean Architecture
principles and the ultra-DRY approach of providing simple data without complex filtering.
"""

from datetime import UTC, datetime
from typing import Any

from attrs import define, field

from src.config import get_logger
from src.domain.entities.track import TrackList
from src.domain.repositories import UnitOfWorkProtocol

logger = get_logger(__name__)


@define(frozen=True, slots=True)
class GetLikedTracksCommand:
    """Command for retrieving liked tracks from canonical database.

    Follows ultra-DRY principle - minimal config with composition through transforms.
    """

    limit: int = 10000  # Maximum tracks to retrieve
    connector_filter: str | None = None  # Optional service filter ("spotify", "lastfm", etc.)
    sort_by: str | None = None  # Optional sorting method
    timestamp: datetime = field(factory=lambda: datetime.now(UTC))

    def validate(self) -> bool:
        """Validate command business rules.

        Returns:
            True if command is valid for execution
        """
        valid_limit = self.limit > 0 and self.limit <= 10000
        
        # Validate sort_by if provided
        valid_sort_options = ["liked_at_desc", "liked_at_asc", "title_asc", "random"]
        valid_sort = self.sort_by is None or self.sort_by in valid_sort_options
        
        return valid_limit and valid_sort


@define(frozen=True, slots=True)
class GetLikedTracksResult:
    """Result of liked tracks retrieval operation.

    Contains the retrieved tracklist and operation metadata.
    """

    tracklist: TrackList
    execution_time_ms: int = 0
    errors: list[str] = field(factory=list)

    @property
    def operation_summary(self) -> dict[str, Any]:
        """Summary of the retrieval operation."""
        return {
            "track_count": len(self.tracklist.tracks),
            "connector_filter": self.tracklist.metadata.get("connector_filter"),
            "execution_time_ms": self.execution_time_ms,
            "success": len(self.errors) == 0,
        }


@define(slots=True)
class GetLikedTracksUseCase:
    """Use case for retrieving liked tracks from canonical database.

    Follows ultra-DRY principle by providing simple data retrieval without
    complex filtering. Users compose complex behavior using existing transforms
    from src/domain/transforms/core.py.

    Clean Architecture compliance:
    - No constructor dependencies (pure domain layer)
    - All repository access through UnitOfWork parameter
    - Business logic separated from workflow orchestration
    """

    async def execute(
        self, command: GetLikedTracksCommand, uow: UnitOfWorkProtocol
    ) -> GetLikedTracksResult:
        """Execute liked tracks retrieval operation.

        Args:
            command: Command with retrieval criteria
            uow: UnitOfWork for repository access

        Returns:
            Result with tracklist and operational metadata

        Raises:
            ValueError: If command validation fails
        """
        if not command.validate():
            raise ValueError("Invalid command: failed business rule validation")

        start_time = datetime.now(UTC)

        logger.info(
            "Retrieving liked tracks",
            limit=command.limit,
            connector_filter=command.connector_filter,
            sort_by=command.sort_by,
        )

        async with uow:
            try:
                tracklist = await self._get_liked_tracks(command, uow)

                # Calculate execution metrics
                execution_time = int(
                    (datetime.now(UTC) - start_time).total_seconds() * 1000
                )

                result = GetLikedTracksResult(
                    tracklist=tracklist,
                    execution_time_ms=execution_time,
                )

                logger.info(
                    "Liked tracks retrieval completed",
                    track_count=len(tracklist.tracks),
                    connector_filter=command.connector_filter,
                    sort_by=command.sort_by,
                    execution_time_ms=execution_time,
                )

                return result

            except Exception as e:
                logger.error(
                    "Liked tracks retrieval failed",
                    error=str(e),
                    connector_filter=command.connector_filter,
                )
                raise

    async def _get_liked_tracks(
        self, command: GetLikedTracksCommand, uow: UnitOfWorkProtocol
    ) -> TrackList:
        """Retrieve liked tracks from database.

        Args:
            command: Command with retrieval criteria
            uow: UnitOfWork for repository access

        Returns:
            TrackList with liked tracks and metadata
        """
        like_repo = uow.get_like_repository()
        track_repo = uow.get_track_repository()

        # Get all liked tracks (filtered by service if specified)
        if command.connector_filter:
            # Get likes for specific service
            track_likes = await like_repo.get_all_liked_tracks(
                service=command.connector_filter, is_liked=True, sort_by=command.sort_by
            )
        else:
            # Get likes across all services
            # Note: This may return duplicates if a track is liked on multiple services
            # Users can apply filter_duplicates transform if needed
            all_services = ["spotify", "lastfm"]  # Could be made configurable
            track_likes = []
            for service in all_services:
                service_likes = await like_repo.get_all_liked_tracks(
                    service=service, is_liked=True, sort_by=command.sort_by
                )
                track_likes.extend(service_likes)

        # Extract track IDs and apply limit
        track_ids = [like.track_id for like in track_likes]
        if len(track_ids) > command.limit:
            track_ids = track_ids[: command.limit]

        # Get tracks in bulk
        tracks_dict = await track_repo.find_tracks_by_ids(track_ids)
        tracks = [tracks_dict[track_id] for track_id in track_ids if track_id in tracks_dict]

        # Create tracklist with metadata for composition
        tracklist = TrackList(
            tracks=tracks,
            metadata={
                "operation": "get_liked_tracks",
                "connector_filter": command.connector_filter,
                "sort_by": command.sort_by,
                "original_likes_count": len(track_likes),
                "track_count": len(tracks),
                "limit_applied": command.limit,
            },
        )

        return tracklist