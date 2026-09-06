"""Update a playlist link's sync direction."""

from uuid import UUID

from attrs import define

from src.application.use_cases._shared.playlist_resolver import mutate_owned_link
from src.domain.entities.playlist_link import PlaylistLink, SyncDirection
from src.domain.repositories.uow import UnitOfWorkProtocol


@define(frozen=True, slots=True)
class UpdatePlaylistLinkCommand:
    """Input: which link to update and the new direction.

    ``playlist_id`` is the parent playlist the caller addressed the link under
    (set by the nested API route). When set, a link belonging to a different
    playlist is reported as missing. Callers that address a link by ID alone
    leave it ``None``.
    """

    user_id: str
    link_id: UUID
    sync_direction: SyncDirection
    playlist_id: UUID | None = None


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
        link = await mutate_owned_link(
            command.link_id,
            uow,
            user_id=command.user_id,
            playlist_id=command.playlist_id,
            mutate=lambda repo: repo.update_link_direction(
                command.link_id, command.sync_direction
            ),
        )
        return UpdatePlaylistLinkResult(link=link)
