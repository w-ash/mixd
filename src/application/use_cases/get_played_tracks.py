"""Service for retrieving music tracks from user's listening history.

Fetches tracks based on play history data, with optional filtering by time period,
music service (Spotify, Last.fm), and sorting preferences. Returns tracks with
play count metadata for further analysis.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from attrs import define, field

from src.application.use_cases._shared.command_validators import (
    optional_in_choices,
    optional_positive_int,
    positive_int_in_range,
)
from src.config import get_logger
from src.config.constants import BusinessLimits
from src.domain.entities import utc_now_factory
from src.domain.entities.track import TrackList
from src.domain.repositories import UnitOfWorkProtocol

logger = get_logger(__name__)


@define(frozen=True, slots=True)
class GetPlayedTracksCommand:
    """Parameters for retrieving tracks from listening history.

    Attributes:
        limit: Maximum number of tracks to return (1-10000).
        days_back: Number of days to look back for plays (None for all time).
        connector_filter: Filter by music service ("spotify", "lastfm", etc.).
        sort_by: Sort order ("played_at_desc", "total_plays_desc", "title_asc", etc.).
        timestamp: When this command was created.
    """

    limit: int = field(
        default=10000,
        validator=positive_int_in_range(1, BusinessLimits.MAX_USER_LIMIT),
    )
    days_back: int | None = field(default=None, validator=optional_positive_int)
    connector_filter: str | None = (
        None  # Optional service filter ("spotify", "lastfm", etc.)
    )
    sort_by: str | None = field(
        default=None,
        validator=optional_in_choices([
            "played_at_desc",
            "total_plays_desc",
            "last_played_desc",
            "first_played_asc",
            "title_asc",
            "random",
        ]),
    )
    timestamp: datetime = field(factory=utc_now_factory)


@define(frozen=True, slots=True)
class GetPlayedTracksResult:
    """Results from retrieving tracks from listening history.

    Attributes:
        tracklist: Retrieved tracks with play count metadata.
        execution_time_ms: How long the operation took in milliseconds.
        errors: Any errors that occurred during retrieval.
    """

    tracklist: TrackList
    execution_time_ms: int = 0
    errors: list[str] = field(factory=list)

    @property
    def operation_summary(self) -> dict[str, Any]:
        """Summary statistics from the track retrieval operation."""
        return {
            "track_count": len(self.tracklist.tracks),
            "days_back": self.tracklist.metadata.get("days_back"),
            "connector_filter": self.tracklist.metadata.get("connector_filter"),
            "execution_time_ms": self.execution_time_ms,
            "success": not self.errors,
        }


@define(slots=True)
class GetPlayedTracksUseCase:
    """Retrieves music tracks from user's listening history database.

    Queries play history records to find tracks the user has listened to,
    with optional filtering by time period and music service. Returns tracks
    with play count metadata that can be used for recommendations or analysis.
    """

    async def execute(
        self, command: GetPlayedTracksCommand, uow: UnitOfWorkProtocol
    ) -> GetPlayedTracksResult:
        """Retrieves tracks from listening history based on specified criteria.

        Args:
            command: Search parameters (limit, time range, service filter, sort order).
            uow: Database connection manager for accessing play and track data.

        Returns:
            Tracks matching criteria with play count metadata and execution stats.

        Raises:
            ValueError: If command execution fails.
        """
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

            except Exception as e:
                logger.error(
                    "Played tracks retrieval failed",
                    error=str(e),
                    days_back=command.days_back,
                    connector_filter=command.connector_filter,
                )
                raise
            else:
                return result

    async def _get_played_tracks(
        self, command: GetPlayedTracksCommand, uow: UnitOfWorkProtocol
    ) -> TrackList:
        """Queries database for tracks matching the search criteria.

        Args:
            command: Search parameters including filters and sorting.
            uow: Database connection manager.

        Returns:
            TrackList with found tracks and play count metadata.
        """
        plays_repo = uow.get_plays_repository()
        track_repo = uow.get_track_repository()

        # Calculate time window if specified
        period_start = None
        if command.days_back:
            period_start = datetime.now(UTC) - timedelta(days=command.days_back)

        # Get recent plays with sorting - repository handles the sorting logic
        recent_plays = await plays_repo.get_recent_plays(
            limit=command.limit * 2, sort_by=command.sort_by
        )

        # Extract unique track IDs from recent plays (filter out None values)
        track_ids = list({
            play.track_id for play in recent_plays if play.track_id is not None
        })

        # Apply connector filter if specified
        if command.connector_filter:
            # Filter plays by connector and extract track IDs
            filtered_plays = [
                play
                for play in recent_plays
                if play.service == command.connector_filter
            ]
            track_ids = list({
                play.track_id for play in filtered_plays if play.track_id is not None
            })

        # Apply limit
        if len(track_ids) > command.limit:
            track_ids = track_ids[: command.limit]

        # Get tracks in bulk
        tracks_dict = await track_repo.find_tracks_by_ids(track_ids)
        tracks = [
            tracks_dict[track_id] for track_id in track_ids if track_id in tracks_dict
        ]

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
