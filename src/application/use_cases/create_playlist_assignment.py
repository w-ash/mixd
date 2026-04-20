"""Create a new PlaylistAssignment.

Thin wrapper over the repository — validates the action value through
the domain entity's ``create`` classmethod (which calls the single-source
``validate_action_value``) and delegates to ``create_assignments``.
"""

from uuid import UUID

from attrs import define

from src.domain.entities.playlist_assignment import (
    AssignmentActionType,
    PlaylistAssignment,
)
from src.domain.repositories import UnitOfWorkProtocol


@define(frozen=True, slots=True)
class CreatePlaylistAssignmentCommand:
    user_id: str
    connector_playlist_id: UUID
    action_type: AssignmentActionType
    raw_action_value: str


@define(frozen=True, slots=True)
class CreatePlaylistAssignmentResult:
    assignment: PlaylistAssignment
    created: bool


@define(slots=True)
class CreatePlaylistAssignmentUseCase:
    async def execute(
        self,
        command: CreatePlaylistAssignmentCommand,
        uow: UnitOfWorkProtocol,
    ) -> CreatePlaylistAssignmentResult:
        async with uow:
            assignment = PlaylistAssignment.create(
                user_id=command.user_id,
                connector_playlist_id=command.connector_playlist_id,
                action_type=command.action_type,
                raw_action_value=command.raw_action_value,
            )
            repo = uow.get_playlist_assignment_repository()
            created = await repo.create_assignments(
                [assignment], user_id=command.user_id
            )
            await uow.commit()

            if created:
                return CreatePlaylistAssignmentResult(
                    assignment=created[0], created=True
                )
            return CreatePlaylistAssignmentResult(assignment=assignment, created=False)


async def run_create_playlist_assignment(
    user_id: str,
    connector_playlist_id: UUID,
    action_type: AssignmentActionType,
    raw_action_value: str,
) -> CreatePlaylistAssignmentResult:
    """Convenience wrapper for CLI / API handlers."""
    from src.application.runner import execute_use_case

    command = CreatePlaylistAssignmentCommand(
        user_id=user_id,
        connector_playlist_id=connector_playlist_id,
        action_type=action_type,
        raw_action_value=raw_action_value,
    )
    return await execute_use_case(
        lambda uow: CreatePlaylistAssignmentUseCase().execute(command, uow),
        user_id=user_id,
    )
