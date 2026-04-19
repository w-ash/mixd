"""Delete a PlaylistMetadataMapping.

Cascade on ``playlist_metadata_mappings.id`` removes the member snapshot
automatically; this use case deletes only the mapping row itself. Cached
preferences/tags written by past imports are NOT cleaned up here — users
who want to remove the effects of a mapping should re-run the metadata
import (which will clear mapping-sourced rows for the now-missing
mapping via the snapshot diff) OR delete the canonical metadata
directly.
"""

from uuid import UUID

from attrs import define

from src.domain.repositories import UnitOfWorkProtocol


@define(frozen=True, slots=True)
class DeletePlaylistMetadataMappingCommand:
    user_id: str
    mapping_id: UUID


@define(frozen=True, slots=True)
class DeletePlaylistMetadataMappingResult:
    deleted: bool


@define(slots=True)
class DeletePlaylistMetadataMappingUseCase:
    async def execute(
        self,
        command: DeletePlaylistMetadataMappingCommand,
        uow: UnitOfWorkProtocol,
    ) -> DeletePlaylistMetadataMappingResult:
        async with uow:
            repo = uow.get_playlist_metadata_mapping_repository()
            deleted = await repo.delete_mapping(
                command.mapping_id, user_id=command.user_id
            )
            if deleted:
                await uow.commit()
            return DeletePlaylistMetadataMappingResult(deleted=deleted)


async def run_delete_playlist_metadata_mapping(
    user_id: str,
    mapping_id: UUID,
) -> DeletePlaylistMetadataMappingResult:
    """Convenience wrapper for CLI / API handlers."""
    from src.application.runner import execute_use_case

    command = DeletePlaylistMetadataMappingCommand(
        user_id=user_id, mapping_id=mapping_id
    )
    return await execute_use_case(
        lambda uow: DeletePlaylistMetadataMappingUseCase().execute(command, uow),
        user_id=user_id,
    )
