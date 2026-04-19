"""Create a new PlaylistMetadataMapping.

Thin wrapper over the repository — validates the action value through
the domain entity's ``create`` classmethod (which calls the single-source
``validate_action_value``) and delegates to ``create_mappings``.
"""

from uuid import UUID

from attrs import define

from src.domain.entities.playlist_metadata_mapping import (
    MappingActionType,
    PlaylistMetadataMapping,
)
from src.domain.repositories import UnitOfWorkProtocol


@define(frozen=True, slots=True)
class CreatePlaylistMetadataMappingCommand:
    user_id: str
    connector_playlist_id: UUID
    action_type: MappingActionType
    raw_action_value: str


@define(frozen=True, slots=True)
class CreatePlaylistMetadataMappingResult:
    mapping: PlaylistMetadataMapping
    created: bool


@define(slots=True)
class CreatePlaylistMetadataMappingUseCase:
    async def execute(
        self,
        command: CreatePlaylistMetadataMappingCommand,
        uow: UnitOfWorkProtocol,
    ) -> CreatePlaylistMetadataMappingResult:
        async with uow:
            mapping = PlaylistMetadataMapping.create(
                user_id=command.user_id,
                connector_playlist_id=command.connector_playlist_id,
                action_type=command.action_type,
                raw_action_value=command.raw_action_value,
            )
            repo = uow.get_playlist_metadata_mapping_repository()
            created = await repo.create_mappings([mapping], user_id=command.user_id)
            await uow.commit()

            if created:
                return CreatePlaylistMetadataMappingResult(
                    mapping=created[0], created=True
                )
            return CreatePlaylistMetadataMappingResult(mapping=mapping, created=False)


async def run_create_playlist_metadata_mapping(
    user_id: str,
    connector_playlist_id: UUID,
    action_type: MappingActionType,
    raw_action_value: str,
) -> CreatePlaylistMetadataMappingResult:
    """Convenience wrapper for CLI / API handlers."""
    from src.application.runner import execute_use_case

    command = CreatePlaylistMetadataMappingCommand(
        user_id=user_id,
        connector_playlist_id=connector_playlist_id,
        action_type=action_type,
        raw_action_value=raw_action_value,
    )
    return await execute_use_case(
        lambda uow: CreatePlaylistMetadataMappingUseCase().execute(command, uow),
        user_id=user_id,
    )
