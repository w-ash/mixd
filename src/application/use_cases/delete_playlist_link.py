"""Delete a link between a canonical playlist and an external service playlist.

Removes the mapping row. The cached DBConnectorPlaylist is kept for potential
re-linking without re-fetching.
"""

from attrs import define

from src.config import get_logger
from src.domain.exceptions import NotFoundError
from src.domain.repositories.interfaces import UnitOfWorkProtocol

logger = get_logger(__name__)


@define(frozen=True, slots=True)
class DeletePlaylistLinkCommand:
    """Input for deleting a playlist link."""

    link_id: int


@define(frozen=True, slots=True)
class DeletePlaylistLinkResult:
    """Output: whether the link was deleted."""

    deleted: bool


@define(slots=True)
class DeletePlaylistLinkUseCase:
    """Delete a playlist link by ID."""

    async def execute(
        self, command: DeletePlaylistLinkCommand, uow: UnitOfWorkProtocol
    ) -> DeletePlaylistLinkResult:
        async with uow:
            link_repo = uow.get_playlist_link_repository()
            deleted = await link_repo.delete_link(command.link_id)

            if not deleted:
                raise NotFoundError(f"Playlist link {command.link_id} not found")

            await uow.commit()

            logger.info("Playlist link deleted", link_id=command.link_id)
            return DeletePlaylistLinkResult(deleted=True)
