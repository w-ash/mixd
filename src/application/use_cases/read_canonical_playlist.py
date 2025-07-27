"""ReadCanonicalPlaylistUseCase for retrieving internal database playlists.

This use case handles reading canonical (internal) playlists from the database
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
class ReadCanonicalPlaylistCommand:
    """Command for reading a canonical playlist.
    
    Encapsulates the identifier and options for retrieving a playlist.
    """

    playlist_id: str
    connector: str | None = None  # Optional connector for external ID lookup
    include_track_metadata: bool = True
    timestamp: datetime = field(factory=lambda: datetime.now(UTC))

    def validate(self) -> bool:
        """Validate command business rules.

        Returns:
            True if command is valid for execution
        """
        return bool(self.playlist_id)


@define(frozen=True, slots=True)
class ReadCanonicalPlaylistResult:
    """Result of canonical playlist read operation.

    Contains the retrieved playlist and operation metadata.
    """

    playlist: Playlist
    execution_time_ms: int = 0
    errors: list[str] = field(factory=list)

    @property
    def operation_summary(self) -> dict[str, Any]:
        """Summary of the read operation."""
        return {
            "playlist_id": self.playlist.id,
            "playlist_name": self.playlist.name,
            "track_count": len(self.playlist.tracks),
            "execution_time_ms": self.execution_time_ms,
            "success": len(self.errors) == 0,
        }


@define(slots=True)
class ReadCanonicalPlaylistUseCase:
    """Use case for reading canonical (internal) playlists.

    Handles pure database read operations following Clean Architecture 
    principles with UnitOfWork pattern:
    - No constructor dependencies (pure domain layer)
    - All repository access through UnitOfWork parameter
    - Simplified testing with single UnitOfWork mock
    """

    async def execute(
        self, command: ReadCanonicalPlaylistCommand, uow: UnitOfWorkProtocol
    ) -> ReadCanonicalPlaylistResult:
        """Execute canonical playlist read operation.

        Args:
            command: Command with playlist read context
            uow: UnitOfWork for repository access

        Returns:
            Result with retrieved playlist and operational metadata

        Raises:
            ValueError: If command validation fails or playlist not found
        """
        if not command.validate():
            raise ValueError("Invalid command: failed business rule validation")

        start_time = datetime.now(UTC)

        logger.info(
            "Reading canonical playlist",
            playlist_id=command.playlist_id,
        )

        async with uow:
            try:
                playlist = await self._get_playlist(command, uow)

                # Calculate execution metrics
                execution_time = int(
                    (datetime.now(UTC) - start_time).total_seconds() * 1000
                )

                result = ReadCanonicalPlaylistResult(
                    playlist=playlist,
                    execution_time_ms=execution_time,
                )

                logger.info(
                    "Canonical playlist read completed",
                    playlist_id=playlist.id,
                    name=playlist.name,
                    track_count=len(playlist.tracks),
                    execution_time_ms=execution_time,
                )

                return result

            except Exception as e:
                logger.error(
                    "Canonical playlist read failed",
                    error=str(e),
                    playlist_id=command.playlist_id,
                )
                raise

    async def _get_playlist(
        self, command: ReadCanonicalPlaylistCommand, uow: UnitOfWorkProtocol
    ) -> Playlist:
        """Retrieve playlist from database.

        Args:
            command: Command with playlist ID and optional connector
            uow: UnitOfWork for repository access

        Returns:
            Playlist entity

        Raises:
            ValueError: If playlist not found
        """
        playlist_repo = uow.get_playlist_repository()

        try:
            # Try to get by internal ID first
            playlist = await playlist_repo.get_playlist_by_id(int(command.playlist_id))
            return playlist
        except ValueError:
            # If not an integer, try as connector ID
            connector_name = command.connector or "spotify"  # Default for backward compatibility
            
            playlist = await playlist_repo.get_playlist_by_connector(
                connector_name, command.playlist_id, raise_if_not_found=True
            )
            
            if playlist is None:
                connector_info = f" (connector: {command.connector})" if command.connector else ""
                raise ValueError(f"Playlist with ID {command.playlist_id}{connector_info} not found") from None
            return playlist