"""List a user's PlaylistMetadataMappings."""

from collections.abc import Sequence

from attrs import define

from src.domain.entities.playlist_metadata_mapping import PlaylistMetadataMapping
from src.domain.repositories import UnitOfWorkProtocol


@define(frozen=True, slots=True)
class ListPlaylistMetadataMappingsCommand:
    user_id: str


@define(frozen=True, slots=True)
class ListPlaylistMetadataMappingsResult:
    mappings: Sequence[PlaylistMetadataMapping]


@define(slots=True)
class ListPlaylistMetadataMappingsUseCase:
    async def execute(
        self,
        command: ListPlaylistMetadataMappingsCommand,
        uow: UnitOfWorkProtocol,
    ) -> ListPlaylistMetadataMappingsResult:
        async with uow:
            repo = uow.get_playlist_metadata_mapping_repository()
            mappings = await repo.list_for_user(user_id=command.user_id)
            return ListPlaylistMetadataMappingsResult(mappings=mappings)


async def run_list_playlist_metadata_mappings(
    user_id: str,
) -> ListPlaylistMetadataMappingsResult:
    """Convenience wrapper for CLI / API handlers."""
    from src.application.runner import execute_use_case

    command = ListPlaylistMetadataMappingsCommand(user_id=user_id)
    return await execute_use_case(
        lambda uow: ListPlaylistMetadataMappingsUseCase().execute(command, uow),
        user_id=user_id,
    )
