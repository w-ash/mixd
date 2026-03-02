"""Retrieves playlist data from the internal database.

Supports lookup by internal database ID or external service ID (Spotify, etc).
Includes execution timing and error handling for playlist not found scenarios.
"""

from datetime import datetime
from typing import Any

from attrs import define, field

from src.application.use_cases._shared.command_validators import non_empty_string
from src.application.use_cases._shared.playlist_resolver import resolve_playlist
from src.application.utilities.timing import ExecutionTimer
from src.config import get_logger
from src.domain.entities import utc_now_factory
from src.domain.entities.playlist import Playlist
from src.domain.repositories import UnitOfWorkProtocol

logger = get_logger(__name__)


@define(frozen=True, slots=True)
class ReadCanonicalPlaylistCommand:
    """Request parameters for playlist retrieval.

    Args:
        playlist_id: Database ID (integer) or external service ID (string)
        connector: External service name (e.g., 'spotify') for ID lookup
        include_track_metadata: Whether to load full track details
        timestamp: Request timestamp for audit logging
    """

    playlist_id: str = field(validator=non_empty_string)
    connector: str | None = None  # Optional connector for external ID lookup
    include_track_metadata: bool = True
    timestamp: datetime = field(factory=utc_now_factory)


@define(frozen=True, slots=True)
class ReadCanonicalPlaylistResult:
    """Response containing retrieved playlist and operation metrics.

    Args:
        playlist: The retrieved playlist with tracks and metadata
        execution_time_ms: Database query execution time in milliseconds
        errors: List of error messages if operation failed
    """

    playlist: Playlist | None
    execution_time_ms: int = 0
    errors: list[str] = field(factory=list)

    @property
    def operation_summary(self) -> dict[str, Any]:
        """Returns operation metrics for logging and monitoring."""
        return {
            "playlist_id": self.playlist.id if self.playlist else None,
            "playlist_name": self.playlist.name if self.playlist else None,
            "track_count": len(self.playlist.tracks) if self.playlist else 0,
            "execution_time_ms": self.execution_time_ms,
            "success": not self.errors,
        }


@define(slots=True)
class ReadCanonicalPlaylistUseCase:
    """Retrieves playlists from database by ID or external service identifier.

    Handles both internal database IDs (integers) and external service IDs
    (strings like Spotify playlist IDs). Provides execution timing and
    structured error handling for monitoring and debugging.
    """

    async def execute(
        self, command: ReadCanonicalPlaylistCommand, uow: UnitOfWorkProtocol
    ) -> ReadCanonicalPlaylistResult:
        """Retrieves playlist from database with timing metrics.

        Validates input, queries database for playlist, and returns structured
        result with execution timing for monitoring.

        Args:
            command: Request parameters including playlist ID and options
            uow: Database transaction handler for repository access

        Returns:
            ReadCanonicalPlaylistResult: Playlist data and operation metrics

        Raises:
            ValueError: If playlist not found
        """
        timer = ExecutionTimer()

        logger.info(
            "Reading canonical playlist",
            playlist_id=command.playlist_id,
        )

        async with uow:
            try:
                playlist = await resolve_playlist(
                    command.playlist_id,
                    uow,
                    connector=command.connector or "spotify",
                    raise_if_not_found=False,
                )

                result = ReadCanonicalPlaylistResult(
                    playlist=playlist,
                    execution_time_ms=timer.stop(),
                )

                if playlist:
                    logger.info(
                        "Canonical playlist read completed",
                        playlist_id=playlist.id,
                        name=playlist.name,
                        track_count=len(playlist.tracks),
                        execution_time_ms=timer.elapsed_ms,
                    )
                else:
                    logger.info(
                        "Canonical playlist not found",
                        playlist_id=command.playlist_id,
                        execution_time_ms=timer.elapsed_ms,
                    )

            except Exception as e:
                logger.error(
                    "Canonical playlist read failed",
                    error=str(e),
                    playlist_id=command.playlist_id,
                )
                raise
            else:
                return result
