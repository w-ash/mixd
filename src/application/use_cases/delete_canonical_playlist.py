"""DeleteCanonicalPlaylistUseCase for removing internal database playlists.

This use case handles deletion of canonical (internal) playlists from the database
following Clean Architecture principles.
"""

from datetime import UTC, datetime
from typing import Any

from attrs import define, field

from src.config import get_logger
from src.domain.entities.playlist import Playlist
from src.domain.repositories import UnitOfWorkProtocol

logger = get_logger(__name__)


@define(frozen=True, slots=True)
class DeleteCanonicalPlaylistCommand:
    """Command for deleting a canonical playlist.

    Encapsulates the identifier and options for deleting a playlist.
    """

    playlist_id: str
    force_delete: bool = (
        False  # Whether to delete even if playlist has external connections
    )
    timestamp: datetime = field(factory=lambda: datetime.now(UTC))

    def validate(self) -> bool:
        """Validate command business rules.

        Returns:
            True if command is valid for execution
        """
        return bool(self.playlist_id)


@define(frozen=True, slots=True)
class DeleteCanonicalPlaylistResult:
    """Result of canonical playlist deletion operation.

    Contains information about the deleted playlist and operation metadata.
    """

    deleted_playlist_id: int
    deleted_playlist_name: str
    tracks_count: int
    execution_time_ms: int = 0
    warnings: list[str] = field(factory=list)
    errors: list[str] = field(factory=list)

    @property
    def operation_summary(self) -> dict[str, Any]:
        """Summary of the deletion operation."""
        return {
            "deleted_playlist_id": self.deleted_playlist_id,
            "deleted_playlist_name": self.deleted_playlist_name,
            "tracks_count": self.tracks_count,
            "execution_time_ms": self.execution_time_ms,
            "warnings_count": len(self.warnings),
            "success": len(self.errors) == 0,
        }


@define(slots=True)
class DeleteCanonicalPlaylistUseCase:
    """Use case for deleting canonical (internal) playlists.

    Handles pure database delete operations following Clean Architecture
    principles with UnitOfWork pattern:
    - No constructor dependencies (pure domain layer)
    - All repository access through UnitOfWork parameter
    - Explicit transaction control in business logic
    - Simplified testing with single UnitOfWork mock
    """

    async def execute(
        self, command: DeleteCanonicalPlaylistCommand, uow: UnitOfWorkProtocol
    ) -> DeleteCanonicalPlaylistResult:
        """Execute canonical playlist deletion operation.

        Args:
            command: Command with playlist deletion context
            uow: UnitOfWork for transaction management and repository access

        Returns:
            Result with deletion confirmation and operational metadata

        Raises:
            ValueError: If command validation fails or playlist not found
        """
        if not command.validate():
            raise ValueError("Invalid command: failed business rule validation")

        start_time = datetime.now(UTC)

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
                if playlist.connector_playlist_ids and not command.force_delete:
                    connected_services = list(playlist.connector_playlist_ids.keys())
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
                    raise ValueError(
                        "Playlist has no ID - cannot delete unsaved playlist"
                    )

                playlist_name = playlist.name
                tracks_count = len(playlist.tracks)

                # Step 4: Delete the playlist
                # Note: We're not deleting the tracks themselves as they might be used in other playlists
                playlist_repo = uow.get_playlist_repository()
                deletion_successful = await playlist_repo.delete_playlist(playlist_id)

                if not deletion_successful:
                    raise ValueError(
                        f"Failed to delete playlist {playlist_id} - it may not exist"
                    )

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

                return result

            except Exception as e:
                # Explicit rollback on business logic failure
                await uow.rollback()
                logger.error(
                    "Canonical playlist deletion failed",
                    error=str(e),
                    playlist_id=command.playlist_id,
                )
                raise

    async def _get_playlist(
        self, playlist_id: str, uow: UnitOfWorkProtocol
    ) -> Playlist:
        """Retrieve playlist from database.

        Args:
            playlist_id: ID of playlist to retrieve
            uow: UnitOfWork for repository access

        Returns:
            Playlist entity

        Raises:
            ValueError: If playlist not found
        """
        playlist_repo = uow.get_playlist_repository()

        try:
            # Try to get by internal ID first
            playlist = await playlist_repo.get_playlist_by_id(int(playlist_id))
            return playlist
        except ValueError:
            # If not an integer, try as connector ID
            playlist = await playlist_repo.get_playlist_by_connector(
                "spotify", playlist_id, raise_if_not_found=True
            )
            if playlist is None:
                raise ValueError(f"Playlist with ID {playlist_id} not found") from None
            return playlist
