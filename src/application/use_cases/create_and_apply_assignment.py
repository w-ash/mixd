"""Create a PlaylistAssignment and immediately apply it to the playlist.

Composes ``CreatePlaylistAssignmentUseCase`` with
``ApplyPlaylistAssignmentsUseCase`` so the web route can create-and-apply in
one call. The create leg commits first; if the apply leg errors, the
assignment persists and can be retried via ``POST /{id}/apply``.
"""

from uuid import UUID

from attrs import define

from src.application.use_cases.apply_playlist_assignments import (
    ApplyPlaylistAssignmentsCommand,
    ApplyPlaylistAssignmentsResult,
    ApplyPlaylistAssignmentsUseCase,
)
from src.application.use_cases.create_playlist_assignment import (
    CreatePlaylistAssignmentCommand,
    CreatePlaylistAssignmentUseCase,
)
from src.domain.entities.playlist_assignment import (
    AssignmentActionType,
    PlaylistAssignment,
)
from src.domain.repositories.uow import UnitOfWorkProtocol


@define(frozen=True, slots=True)
class CreateAndApplyAssignmentCommand:
    user_id: str
    connector_playlist_id: UUID
    action_type: AssignmentActionType
    raw_action_value: str


@define(frozen=True, slots=True)
class CreateAndApplyAssignmentResult:
    assignment: PlaylistAssignment
    apply_result: ApplyPlaylistAssignmentsResult


@define(slots=True)
class CreateAndApplyAssignmentUseCase:
    """Create an assignment, then apply it to the playlist's tracks."""

    async def execute(
        self, command: CreateAndApplyAssignmentCommand, uow: UnitOfWorkProtocol
    ) -> CreateAndApplyAssignmentResult:
        create_result = await CreatePlaylistAssignmentUseCase().execute(
            CreatePlaylistAssignmentCommand(
                user_id=command.user_id,
                connector_playlist_id=command.connector_playlist_id,
                action_type=command.action_type,
                raw_action_value=command.raw_action_value,
            ),
            uow,
        )
        apply_result = await ApplyPlaylistAssignmentsUseCase().execute(
            ApplyPlaylistAssignmentsCommand(
                user_id=command.user_id,
                assignment_ids=[create_result.assignment.id],
            ),
            uow,
        )
        return CreateAndApplyAssignmentResult(
            assignment=create_result.assignment, apply_result=apply_result
        )
