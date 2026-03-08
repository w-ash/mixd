"""Use case for listing all stored playlists following DDD principles.

Application layer that coordinates domain operations through dependency injection
and UnitOfWork pattern, without direct infrastructure dependencies.
"""

from attrs import define

from src.domain.entities import Playlist
from src.domain.repositories.interfaces import UnitOfWorkProtocol


@define(frozen=True, slots=True)
class ListPlaylistsCommand:
    """Parameterless — exists for API uniformity."""


@define(frozen=True, slots=True)
class ListPlaylistsResult:
    """Result of listing playlists operation."""

    playlists: list[Playlist]
    total_count: int

    @property
    def has_playlists(self) -> bool:
        """Check if any playlists were found."""
        return self.total_count > 0


@define(slots=True)
class ListPlaylistsUseCase:
    """Use case for retrieving all stored playlists."""

    async def execute(
        self, command: ListPlaylistsCommand, uow: UnitOfWorkProtocol
    ) -> ListPlaylistsResult:
        """Execute the playlist listing operation.

        Args:
            command: Parameterless command for API uniformity.
            uow: Unit of work for repository access.

        Returns:
            ListPlaylistsResult containing playlists and metadata.
        """
        async with uow:
            playlist_repo = uow.get_playlist_repository()
            playlists = await playlist_repo.list_all_playlists()

            return ListPlaylistsResult(
                playlists=playlists,
                total_count=len(playlists),
            )
