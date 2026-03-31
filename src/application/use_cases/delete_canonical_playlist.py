"""Deletes playlists from the internal database with transaction safety.

Handles deletion of playlists stored in the application's database, including
validation of playlist existence, optional warnings for external connections,
and atomic transaction management to ensure data consistency.
"""

# pyright: reportExplicitAny=false
# Legitimate Any: operation_summary return type (line 64)

from datetime import datetime
from typing import Any, Never
from uuid import UUID

from attrs import define, field

from src.application.use_cases._shared.command_validators import non_empty_string
from src.application.use_cases._shared.playlist_resolver import require_playlist
from src.application.utilities.timing import ExecutionTimer
from src.config import get_logger
from src.domain.entities import utc_now_factory
from src.domain.repositories import UnitOfWorkProtocol

logger = get_logger(__name__)


@define(frozen=True, slots=True)
class DeleteCanonicalPlaylistCommand:
    """Input parameters for playlist deletion operation.

    Args:
        playlist_id: Internal database ID or external connector ID of playlist to delete.
        force_delete: Whether to delete playlist even if it has external service connections.
        timestamp: When the deletion command was created (defaults to current UTC time).
    """

    user_id: str
    playlist_id: str = field(validator=non_empty_string)
    force_delete: bool = (
        False  # Whether to delete even if playlist has external connections
    )
    timestamp: datetime = field(factory=utc_now_factory)


@define(frozen=True, slots=True)
class DeleteCanonicalPlaylistResult:
    """Results and metadata from a completed playlist deletion operation.

    Args:
        deleted_playlist_id: Database ID of the deleted playlist.
        deleted_playlist_name: Name of the deleted playlist.
        tracks_count: Number of tracks that were in the deleted playlist.
        execution_time_ms: Time taken to complete the deletion operation in milliseconds.
        warnings: List of warning messages generated during deletion.
        errors: List of error messages if deletion failed.
    """

    deleted_playlist_id: UUID
    deleted_playlist_name: str
    tracks_count: int
    execution_time_ms: int = 0
    warnings: list[str] = field(factory=list)
    errors: list[str] = field(factory=list)

    @property
    def operation_summary(self) -> dict[str, Any]:
        """Returns a structured summary of the deletion operation for logging or APIs."""
        return {
            "deleted_playlist_id": self.deleted_playlist_id,
            "deleted_playlist_name": self.deleted_playlist_name,
            "tracks_count": self.tracks_count,
            "execution_time_ms": self.execution_time_ms,
            "warnings_count": len(self.warnings),
            "success": not self.errors,
        }


@define(slots=True)
class DeleteCanonicalPlaylistUseCase:
    """Orchestrates safe deletion of playlists from the internal database.

    Provides atomic playlist deletion with validation, metadata collection,
    and optional warnings for playlists connected to external services.
    Accepts either internal database IDs or external service IDs for lookup.
    """

    async def execute(
        self, command: DeleteCanonicalPlaylistCommand, uow: UnitOfWorkProtocol
    ) -> DeleteCanonicalPlaylistResult:
        """Deletes a playlist from the database with transaction safety.

        Validates playlist existence, collects metadata, optionally warns about
        external connections, and atomically removes the playlist while preserving
        any tracks that may be used in other playlists.

        Args:
            command: Deletion parameters including playlist ID and options.
            uow: Unit of work for transaction management and repository access.

        Returns:
            Result containing deletion confirmation and operation metadata.

        Raises:
            ValueError: If playlist not found.
        """
        timer = ExecutionTimer()

        def _raise_no_id_error() -> Never:
            raise ValueError("Playlist has no ID - cannot delete unsaved playlist")

        def _raise_deletion_failed_error(playlist_id: UUID) -> Never:
            raise ValueError(
                f"Failed to delete playlist {playlist_id} - it may not exist"
            )

        logger.info(
            "Starting canonical playlist deletion",
            playlist_id=command.playlist_id,
            force_delete=command.force_delete,
        )

        async with uow:
            try:
                # Step 1: Get current playlist to ensure it exists and collect metadata
                playlist = await require_playlist(
                    command.playlist_id, uow, user_id=command.user_id
                )

                # Step 2: Check for external connections and warn if needed
                warnings: list[str] = []
                if playlist.connector_playlist_identifiers and not command.force_delete:
                    connected_services = list(
                        playlist.connector_playlist_identifiers.keys()
                    )
                    warnings.append(
                        f"Playlist is connected to external services: {connected_services}. Use force_delete=True to delete anyway."
                    )

                    # For now, we'll proceed but log the warning
                    # In the future, this could be a configurable behavior
                    logger.warning(
                        "Deleting playlist with external connections",
                        playlist_id=playlist.id,
                        connected_services=connected_services,
                    )

                # Step 3: Collect metadata before deletion
                playlist_id = playlist.id
                if playlist_id is None:
                    _raise_no_id_error()

                playlist_name = playlist.name
                tracks_count = len(playlist.tracks)

                # Step 4: Delete the playlist
                # Note: We're not deleting the tracks themselves as they might be used in other playlists
                playlist_repo = uow.get_playlist_repository()
                deletion_successful = await playlist_repo.delete_playlist(
                    playlist_id, user_id=command.user_id
                )

                if not deletion_successful:
                    _raise_deletion_failed_error(playlist_id)

                # Step 5: Commit transaction
                await uow.commit()

                result = DeleteCanonicalPlaylistResult(
                    deleted_playlist_id=playlist_id or 0,
                    deleted_playlist_name=playlist_name,
                    tracks_count=tracks_count,
                    execution_time_ms=timer.stop(),
                    warnings=warnings,
                )

                logger.info(
                    "Canonical playlist deletion completed",
                    deleted_playlist_id=playlist_id,
                    deleted_playlist_name=playlist_name,
                    tracks_count=tracks_count,
                    execution_time_ms=timer.elapsed_ms,
                )

            except Exception as e:
                # Explicit rollback on business logic failure
                await uow.rollback()
                logger.error(
                    "Canonical playlist deletion failed",
                    error=str(e),
                    playlist_id=command.playlist_id,
                )
                raise
            else:
                return result
