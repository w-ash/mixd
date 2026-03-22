"""Retrieves user's liked tracks from music database.

Fetches tracks that users have marked as liked on music services (Spotify, Last.fm).
Supports filtering by service, sorting options, and limiting results count.
"""

# pyright: reportExplicitAny=false, reportAny=false
# Legitimate Any: use case results, OperationResult metadata, metric values

from datetime import datetime
from typing import Any

from attrs import define, field

from src.application.use_cases._shared.command_validators import (
    optional_in_choices,
    positive_int_in_range,
)
from src.application.utilities.timing import ExecutionTimer
from src.config import get_logger
from src.config.constants import BusinessLimits
from src.domain.entities import utc_now_factory
from src.domain.entities.track import TrackList
from src.domain.repositories import UnitOfWorkProtocol

logger = get_logger(__name__)


@define(frozen=True, slots=True)
class GetLikedTracksCommand:
    """Configuration for retrieving liked tracks.

    Attributes:
        limit: Maximum number of tracks to return.
        connector_filter: Optional service name to filter by ("spotify", "lastfm").
        sort_by: Optional sort method ("liked_at_desc", "liked_at_asc", "title_asc", "random").
        timestamp: When the command was created.
    """

    limit: int = field(
        default=BusinessLimits.DEFAULT_LIBRARY_QUERY_LIMIT,
        validator=positive_int_in_range(),
    )
    connector_filter: str | None = (
        None  # Optional service filter ("spotify", "lastfm", etc.)
    )
    sort_by: str | None = field(
        default=None,
        validator=optional_in_choices([
            "liked_at_desc",
            "liked_at_asc",
            "title_asc",
            "random",
        ]),
    )
    timestamp: datetime = field(factory=utc_now_factory)


@define(frozen=True, slots=True)
class GetLikedTracksResult:
    """Result of liked tracks retrieval.

    Attributes:
        tracklist: Retrieved tracks with metadata.
        total_available: Total tracks before limit was applied.
        execution_time_ms: How long the operation took in milliseconds.
        errors: List of error messages if any occurred.
    """

    tracklist: TrackList
    total_available: int = 0
    execution_time_ms: int = 0
    errors: list[str] = field(factory=list)

    @property
    def operation_summary(self) -> dict[str, Any]:
        """Summary stats for the retrieval operation."""
        return {
            "track_count": len(self.tracklist.tracks),
            "execution_time_ms": self.execution_time_ms,
            "success": not self.errors,
        }


@define(slots=True)
class GetLikedTracksUseCase:
    """Service for retrieving user's liked tracks from music database.

    Fetches tracks marked as liked on music services, with optional filtering by
    service (Spotify/Last.fm) and sorting. Returns up to 10,000 tracks per request.
    """

    async def execute(
        self, command: GetLikedTracksCommand, uow: UnitOfWorkProtocol
    ) -> GetLikedTracksResult:
        """Retrieves liked tracks based on command criteria.

        Args:
            command: Configuration for which tracks to retrieve.
            uow: Database connection manager.

        Returns:
            Retrieved tracks with execution metadata.

        Raises:
            ValueError: If command execution fails.
        """
        timer = ExecutionTimer()

        logger.info(
            "Retrieving liked tracks",
            limit=command.limit,
            connector_filter=command.connector_filter,
            sort_by=command.sort_by,
        )

        async with uow:
            try:
                tracklist, total_available = await self._get_liked_tracks(command, uow)

                result = GetLikedTracksResult(
                    tracklist=tracklist,
                    total_available=total_available,
                    execution_time_ms=timer.stop(),
                )

                logger.info(
                    "Liked tracks retrieval completed",
                    track_count=len(tracklist.tracks),
                    connector_filter=command.connector_filter,
                    sort_by=command.sort_by,
                    execution_time_ms=timer.elapsed_ms,
                )

            except Exception as e:
                logger.error(
                    "Liked tracks retrieval failed",
                    error=str(e),
                    connector_filter=command.connector_filter,
                )
                raise
            else:
                return result

    async def _get_liked_tracks(
        self, command: GetLikedTracksCommand, uow: UnitOfWorkProtocol
    ) -> tuple[TrackList, int]:
        """Fetches liked tracks from database repositories.

        Args:
            command: Configuration for which tracks to retrieve.
            uow: Database connection manager.

        Returns:
            Tuple of (TrackList with liked tracks, total available before limit).
        """
        like_repo = uow.get_like_repository()
        track_repo = uow.get_track_repository()

        # Get all liked tracks (filtered by service if specified)
        if command.connector_filter:
            # Get likes for specific connector service
            track_likes = await like_repo.get_all_liked_tracks(
                service=command.connector_filter, is_liked=True, sort_by=command.sort_by
            )
        else:
            # Query canonical "mixd" service — the source of truth for all likes
            track_likes = await like_repo.get_all_liked_tracks(
                service="mixd", is_liked=True, sort_by=command.sort_by
            )

        # Extract track IDs and apply limit
        track_ids = [like.track_id for like in track_likes]
        total_available = len(track_ids)
        if len(track_ids) > command.limit:
            track_ids = track_ids[: command.limit]

        # Get tracks in bulk
        tracks_dict = await track_repo.find_tracks_by_ids(track_ids)
        tracks = [
            tracks_dict[track_id] for track_id in track_ids if track_id in tracks_dict
        ]

        # Create tracklist with operational metadata
        tracklist = TrackList(
            tracks=tracks,
            metadata={
                "operation": "get_liked_tracks",
            },
        )

        return tracklist, total_available
