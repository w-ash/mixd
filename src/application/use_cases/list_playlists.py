"""Use case for listing all stored playlists following DDD principles.

Application layer that coordinates domain operations through dependency injection
and UnitOfWork pattern, without direct infrastructure dependencies.
"""

from __future__ import annotations

from attrs import define

from src.domain.entities import Playlist
from src.domain.repositories.interfaces import UnitOfWorkProtocol


@define(frozen=True, slots=True)
class ListPlaylistsResult:
    """Result of listing playlists operation."""

    playlists: list[Playlist]
    total_count: int

    @property
    def has_playlists(self) -> bool:
        """Check if any playlists were found."""
        return self.total_count > 0


class ListPlaylistsUseCase:
    """Use case for retrieving all stored playlists.

    Follows DDD principles:
    - Application layer coordinates the operation
    - Uses dependency injection through UnitOfWork
    - Returns domain entities
    - No direct infrastructure dependencies
    """

    def __init__(self, unit_of_work: UnitOfWorkProtocol) -> None:
        """Initialize with unit of work for dependency injection.

        Args:
            unit_of_work: UnitOfWork instance providing repository access
        """
        self._unit_of_work = unit_of_work

    async def execute(self) -> ListPlaylistsResult:
        """Execute the playlist listing operation.

        Returns:
            ListPlaylistsResult containing playlists and metadata
        """
        async with self._unit_of_work as uow:
            # Get all playlists through the domain repository interface
            playlist_repo = uow.get_playlist_repository()
            playlists = await playlist_repo.list_all_playlists()

            # Return structured result
            return ListPlaylistsResult(
                playlists=playlists,
                total_count=len(playlists),
            )
