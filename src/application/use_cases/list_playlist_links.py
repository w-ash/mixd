"""List all connector links for a canonical playlist."""

from uuid import UUID

from attrs import define

from src.domain.entities.playlist_link import PlaylistLink
from src.domain.repositories.interfaces import UnitOfWorkProtocol


@define(frozen=True, slots=True)
class ListPlaylistLinksCommand:
    """Input: which playlist to list links for."""

    playlist_id: UUID


@define(frozen=True, slots=True)
class ListPlaylistLinksResult:
    """Output: the links found."""

    links: list[PlaylistLink]


@define(slots=True)
class ListPlaylistLinksUseCase:
    """Query all connector links for a canonical playlist."""

    async def execute(
        self, command: ListPlaylistLinksCommand, uow: UnitOfWorkProtocol
    ) -> ListPlaylistLinksResult:
        async with uow:
            link_repo = uow.get_playlist_link_repository()
            links = await link_repo.get_links_for_playlist(command.playlist_id)
            return ListPlaylistLinksResult(links=links)
