"""Retrieves playlist data from the internal database.

Supports lookup by internal database ID or external service ID (Spotify, etc).
Includes execution timing and error handling for playlist not found scenarios.
"""

from datetime import datetime

from attrs import define, field

from src.application.use_cases._shared.command_validators import non_empty_string
from src.application.use_cases._shared.playlist_resolver import (
    require_playlist,
    resolve_playlist,
)
from src.application.use_cases._shared.timed_execution import timed_query
from src.config import get_logger
from src.config.constants import BusinessLimits
from src.domain.entities import utc_now_factory
from src.domain.entities.playlist import Playlist, PlaylistEntry
from src.domain.repositories.uow import UnitOfWorkProtocol

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

    user_id: str
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
    def operation_summary(self) -> dict[str, object]:
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
        logger.info(
            "Reading canonical playlist",
            playlist_id=command.playlist_id,
        )

        async with (
            uow,
            timed_query(
                "Canonical playlist read",
                error_log_context={"playlist_id": command.playlist_id},
            ) as timer,
        ):
            playlist = await resolve_playlist(
                command.playlist_id,
                uow,
                user_id=command.user_id,
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

            return result


@define(frozen=True, slots=True)
class ReadPlaylistTracksPageCommand:
    """Request parameters for a paginated slice of a playlist's entries."""

    user_id: str
    playlist_id: str = field(validator=non_empty_string)
    limit: int = field(default=BusinessLimits.DEFAULT_PAGE_SIZE)
    offset: int = 0
    connector: str | None = None


@define(frozen=True, slots=True)
class ReadPlaylistTracksPageResult:
    """A page of playlist entries plus the total entry count."""

    entries: list[PlaylistEntry]
    total: int
    limit: int
    offset: int


@define(slots=True)
class ReadPlaylistTracksPageUseCase:
    """Return one page of a playlist's entries (offset/limit slice + total).

    Owns the slicing that used to live in the ``GET /playlists/{id}/tracks``
    handler. Resolves the playlist (404 if missing/not owned) and paginates
    its entries in the application layer.
    """

    async def execute(
        self, command: ReadPlaylistTracksPageCommand, uow: UnitOfWorkProtocol
    ) -> ReadPlaylistTracksPageResult:
        async with uow:
            playlist = await require_playlist(
                command.playlist_id,
                uow,
                user_id=command.user_id,
                connector=command.connector or "spotify",
            )
            entries = playlist.entries
            page = entries[command.offset : command.offset + command.limit]
            return ReadPlaylistTracksPageResult(
                entries=page,
                total=len(entries),
                limit=command.limit,
                offset=command.offset,
            )
