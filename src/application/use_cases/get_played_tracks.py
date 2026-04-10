"""Service for retrieving music tracks from user's listening history.

Fetches tracks based on play history data, with optional filtering by time period,
music service (Spotify, Last.fm), and sorting preferences. Returns tracks with
play count metadata for further analysis.
"""

from datetime import UTC, datetime, timedelta
from typing import cast

from attrs import define, field
from attrs.validators import and_, ge, gt, in_, instance_of, le, optional

from src.application.utilities.timing import ExecutionTimer
from src.config import get_logger
from src.config.constants import BusinessLimits
from src.domain.entities import utc_now_factory
from src.domain.entities.track import TrackList, TrackListMetadata
from src.domain.repositories import UnitOfWorkProtocol
from src.domain.repositories.interfaces import PlaySortBy

logger = get_logger(__name__)


@define(frozen=True, slots=True)
class GetPlayedTracksCommand:
    """Parameters for retrieving tracks from listening history.

    Attributes:
        user_id: Owning user.
        limit: Maximum number of tracks to return.
        days_back: Number of days to look back for plays (None for all time).
        connector_filter: Filter by music service ("spotify", "lastfm", etc.).
        sort_by: Sort order ("played_at_desc", "total_plays_desc", "title_asc", etc.).
        timestamp: When this command was created.
    """

    user_id: str
    limit: int = field(
        default=BusinessLimits.DEFAULT_LIBRARY_QUERY_LIMIT,
        validator=and_(instance_of(int), ge(1), le(BusinessLimits.MAX_USER_LIMIT)),
    )
    days_back: int | None = field(
        default=None, validator=optional([instance_of(int), gt(0)])
    )
    connector_filter: str | None = (
        None  # Optional service filter ("spotify", "lastfm", etc.)
    )
    sort_by: PlaySortBy | None = field(
        default=None,
        validator=optional(
            in_([
                "played_at_desc",
                "total_plays_desc",
                "last_played_desc",
                "first_played_asc",
                "title_asc",
                "random",
            ])
        ),
    )
    timestamp: datetime = field(factory=utc_now_factory)


@define(frozen=True, slots=True)
class GetPlayedTracksResult:
    """Results from retrieving tracks from listening history.

    Attributes:
        tracklist: Retrieved tracks with play count metadata.
        total_available: Total tracks before limit was applied.
        execution_time_ms: How long the operation took in milliseconds.
        errors: Any errors that occurred during retrieval.
    """

    tracklist: TrackList
    total_available: int = 0
    execution_time_ms: int = 0
    errors: list[str] = field(factory=list)

    @property
    def operation_summary(self) -> dict[str, object]:
        """Summary statistics from the track retrieval operation."""
        return {
            "track_count": len(self.tracklist.tracks),
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
        timer = ExecutionTimer()

        logger.info(
            "Retrieving played tracks",
            limit=command.limit,
            days_back=command.days_back,
            connector_filter=command.connector_filter,
            sort_by=command.sort_by,
        )

        async with uow:
            try:
                tracklist, total_available = await self._get_played_tracks(command, uow)

                result = GetPlayedTracksResult(
                    tracklist=tracklist,
                    total_available=total_available,
                    execution_time_ms=timer.stop(),
                )

                logger.info(
                    "Played tracks retrieval completed",
                    track_count=len(tracklist.tracks),
                    days_back=command.days_back,
                    connector_filter=command.connector_filter,
                    sort_by=command.sort_by,
                    execution_time_ms=timer.elapsed_ms,
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
    ) -> tuple[TrackList, int]:
        """Queries database for tracks matching the search criteria.

        Args:
            command: Search parameters including filters and sorting.
            uow: Database connection manager.

        Returns:
            Tuple of (TrackList with play count metadata, total available before limit).
        """
        plays_repo = uow.get_plays_repository()
        track_repo = uow.get_track_repository()

        # Calculate time window if specified
        period_start = None
        if command.days_back:
            period_start = datetime.now(UTC) - timedelta(days=command.days_back)

        # Get recent plays with sorting - repository handles the sorting logic
        recent_plays = await plays_repo.get_recent_plays(
            limit=command.limit * 2,
            sort_by=command.sort_by,
            user_id=command.user_id,
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
        total_available = len(track_ids)
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
            user_id=command.user_id,
        )

        # Create tracklist with play metrics in canonical nested structure
        # Cast needed: dict invariance means dict[int, int] ≠ dict[int, MetricValue]
        tracklist = TrackList(
            tracks=tracks,
            metadata=cast(
                TrackListMetadata,
                {
                    "operation": "get_played_tracks",
                    "metrics": {
                        "total_plays": play_metrics.get("total_plays", {}),
                        "last_played_dates": play_metrics.get("last_played_dates", {}),
                    },
                },
            ),
        )

        return tracklist, total_available
