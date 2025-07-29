"""GetPlayedTracksUseCase for retrieving played tracks from canonical database.

This use case handles reading tracks from play history following Clean Architecture
principles and the ultra-DRY approach of providing simple data without complex filtering.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from attrs import define, field

from src.config import get_logger
from src.domain.entities.track import TrackList
from src.domain.repositories import UnitOfWorkProtocol

logger = get_logger(__name__)


@define(frozen=True, slots=True)
class GetPlayedTracksCommand:
    """Command for retrieving tracks from play history.

    Follows ultra-DRY principle - minimal config with composition through transforms.
    """

    limit: int = 10000  # Maximum tracks to retrieve
    days_back: int | None = None  # Optional time window (e.g., last 90 days)
    connector_filter: str | None = None  # Optional service filter ("spotify", "lastfm", etc.)
    sort_by: str | None = None  # Optional sorting method
    timestamp: datetime = field(factory=lambda: datetime.now(UTC))

    def validate(self) -> bool:
        """Validate command business rules.

        Returns:
            True if command is valid for execution
        """
        valid_limit = self.limit > 0 and self.limit <= 10000
        valid_days = self.days_back is None or self.days_back > 0
        
        # Validate sort_by if provided
        valid_sort_options = ["played_at_desc", "total_plays_desc", "last_played_desc", "first_played_asc", "title_asc", "random"]
        valid_sort = self.sort_by is None or self.sort_by in valid_sort_options
        
        return valid_limit and valid_days and valid_sort


@define(frozen=True, slots=True)
class GetPlayedTracksResult:
    """Result of played tracks retrieval operation.

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
            "days_back": self.tracklist.metadata.get("days_back"),
            "connector_filter": self.tracklist.metadata.get("connector_filter"),
            "execution_time_ms": self.execution_time_ms,
            "success": len(self.errors) == 0,
        }


@define(slots=True)
class GetPlayedTracksUseCase:
    """Use case for retrieving tracks from play history.

    Follows ultra-DRY principle by providing simple data retrieval without
    complex filtering. Users compose complex behavior using existing transforms
    from src/domain/transforms/core.py like filter_by_play_history and
    sort_by_play_history.

    Clean Architecture compliance:
    - No constructor dependencies (pure domain layer)
    - All repository access through UnitOfWork parameter
    - Business logic separated from workflow orchestration
    """

    async def execute(
        self, command: GetPlayedTracksCommand, uow: UnitOfWorkProtocol
    ) -> GetPlayedTracksResult:
        """Execute played tracks retrieval operation.

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
            "Retrieving played tracks",
            limit=command.limit,
            days_back=command.days_back,
            connector_filter=command.connector_filter,
            sort_by=command.sort_by,
        )

        async with uow:
            try:
                tracklist = await self._get_played_tracks(command, uow)

                # Calculate execution metrics
                execution_time = int(
                    (datetime.now(UTC) - start_time).total_seconds() * 1000
                )

                result = GetPlayedTracksResult(
                    tracklist=tracklist,
                    execution_time_ms=execution_time,
                )

                logger.info(
                    "Played tracks retrieval completed",
                    track_count=len(tracklist.tracks),
                    days_back=command.days_back,
                    connector_filter=command.connector_filter,
                    sort_by=command.sort_by,
                    execution_time_ms=execution_time,
                )

                return result

            except Exception as e:
                logger.error(
                    "Played tracks retrieval failed",
                    error=str(e),
                    days_back=command.days_back,
                    connector_filter=command.connector_filter,
                )
                raise

    async def _get_played_tracks(
        self, command: GetPlayedTracksCommand, uow: UnitOfWorkProtocol
    ) -> TrackList:
        """Retrieve tracks from play history.

        Args:
            command: Command with retrieval criteria
            uow: UnitOfWork for repository access

        Returns:
            TrackList with played tracks and metadata including play metrics
        """
        plays_repo = uow.get_plays_repository()
        track_repo = uow.get_track_repository()

        # Calculate time window if specified
        period_start = None
        if command.days_back:
            period_start = datetime.now(UTC) - timedelta(days=command.days_back)

        # Get recent plays with sorting - repository handles the sorting logic
        recent_plays = await plays_repo.get_recent_plays(limit=command.limit * 2, sort_by=command.sort_by)

        # Extract unique track IDs from recent plays (filter out None values)
        track_ids = list({play.track_id for play in recent_plays if play.track_id is not None})
        
        # Apply connector filter if specified
        if command.connector_filter:
            # Filter plays by connector and extract track IDs
            filtered_plays = [play for play in recent_plays if play.service == command.connector_filter]
            track_ids = list({play.track_id for play in filtered_plays if play.track_id is not None})

        # Apply limit
        if len(track_ids) > command.limit:
            track_ids = track_ids[:command.limit]

        # Get tracks in bulk
        tracks_dict = await track_repo.find_tracks_by_ids(track_ids)
        tracks = [tracks_dict[track_id] for track_id in track_ids if track_id in tracks_dict]

        # Get play aggregations for these tracks to provide metadata for transforms
        play_metrics = await plays_repo.get_play_aggregations(
            track_ids=track_ids,
            metrics=["total_plays", "last_played_dates"],
            period_start=period_start,
            period_end=None,
        )

        # Create tracklist with play metrics metadata for composition with transforms
        tracklist = TrackList(
            tracks=tracks,
            metadata={
                "operation": "get_played_tracks",
                "days_back": command.days_back,
                "connector_filter": command.connector_filter,
                "sort_by": command.sort_by,
                "period_start": period_start.isoformat() if period_start else None,
                "track_count": len(tracks),
                "limit_applied": command.limit,
                # Include play metrics for use by transforms like filter_by_play_history
                "total_plays": play_metrics.get("total_plays", {}),
                "last_played_dates": play_metrics.get("last_played_dates", {}),
                # Also store in nested structure expected by some transforms
                "metrics": {
                    "total_plays": play_metrics.get("total_plays", {}),
                    "last_played_dates": play_metrics.get("last_played_dates", {}),
                },
            },
        )

        return tracklist