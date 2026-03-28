"""Update a playlist link's sync direction."""

from uuid import UUID

from attrs import define

from src.domain.entities.playlist_link import PlaylistLink, SyncDirection
from src.domain.exceptions import NotFoundError
from src.domain.repositories.interfaces import UnitOfWorkProtocol


@define(frozen=True, slots=True)
class UpdatePlaylistLinkCommand:
    """Input: which link to update and the new direction."""

    link_id: UUID
    sync_direction: SyncDirection


@define(frozen=True, slots=True)
class UpdatePlaylistLinkResult:
    """Output: the updated link."""

    link: PlaylistLink


@define(slots=True)
class UpdatePlaylistLinkUseCase:
    """Update a playlist link's sync direction."""

    async def execute(
        self, command: UpdatePlaylistLinkCommand, uow: UnitOfWorkProtocol
    ) -> UpdatePlaylistLinkResult:
        async with uow:
            link_repo = uow.get_playlist_link_repository()
            link = await link_repo.update_link_direction(
                command.link_id, command.sync_direction
            )
            if link is None:
                raise NotFoundError(f"Playlist link {command.link_id} not found")
            await uow.commit()
            return UpdatePlaylistLinkResult(link=link)
