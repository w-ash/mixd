"""Deletes playlists from the internal database with transaction safety.

Handles deletion of playlists stored in the application's database, including
validation of playlist existence, optional warnings for external connections,
and atomic transaction management to ensure data consistency.
"""

from datetime import UTC, datetime
from typing import Any, Never

from attrs import define, field

from src.application.use_cases._shared.command_validators import non_empty_string
from src.config import get_logger
from src.domain.entities import utc_now_factory
from src.domain.entities.playlist import Playlist
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

    deleted_playlist_id: int
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
        start_time = datetime.now(UTC)

        def _raise_no_id_error() -> Never:
            raise ValueError("Playlist has no ID - cannot delete unsaved playlist")

        def _raise_deletion_failed_error(playlist_id: int) -> Never:
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
                playlist = await self._get_playlist(command.playlist_id, uow)

                # Step 2: Check for external connections and warn if needed
                warnings = []
                if playlist.connector_playlist_identifiers and not command.force_delete:
                    connected_services = list(
                        playlist.connector_playlist_identifiers.keys()
                    )
                    warnings.append(
                        f"Playlist is connected to external services: {connected_services}. "
                        "Use force_delete=True to delete anyway."
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
                deletion_successful = await playlist_repo.delete_playlist(playlist_id)

                if not deletion_successful:
                    _raise_deletion_failed_error(playlist_id)

                # Step 5: Commit transaction
                await uow.commit()

                # Step 6: Calculate execution metrics
                execution_time = int(
                    (datetime.now(UTC) - start_time).total_seconds() * 1000
                )

                result = DeleteCanonicalPlaylistResult(
                    deleted_playlist_id=playlist_id or 0,
                    deleted_playlist_name=playlist_name,
                    tracks_count=tracks_count,
                    execution_time_ms=execution_time,
                    warnings=warnings,
                )

                logger.info(
                    "Canonical playlist deletion completed",
                    deleted_playlist_id=playlist_id,
                    deleted_playlist_name=playlist_name,
                    tracks_count=tracks_count,
                    execution_time_ms=execution_time,
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

    async def _get_playlist(
        self, playlist_id: str, uow: UnitOfWorkProtocol
    ) -> Playlist:
        """Retrieves a playlist by internal ID or external connector ID.

        Attempts to find playlist by internal database ID first, then falls back
        to searching by Spotify connector ID if the input is not numeric.

        Args:
            playlist_id: Internal database ID (numeric) or external service ID.
            uow: Unit of work for repository access.

        Returns:
            The found playlist entity.

        Raises:
            ValueError: If no playlist found with the given ID.
        """
        playlist_repo = uow.get_playlist_repository()

        try:
            # Try to get by internal ID first
            playlist = await playlist_repo.get_playlist_by_id(int(playlist_id))
        except ValueError:
            # If not an integer, try as connector ID
            playlist = await playlist_repo.get_playlist_by_connector(
                "spotify", playlist_id, raise_if_not_found=True
            )
            if playlist is None:
                raise ValueError(f"Playlist with ID {playlist_id} not found") from None
            return playlist
        else:
            return playlist
