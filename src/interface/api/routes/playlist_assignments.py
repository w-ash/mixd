"""Playlist assignment endpoints.

All three routes are single-assignment. Bulk apply lives only in the CLI
(``mixd playlist apply-assignments``).
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from src.application.runner import execute_use_case
from src.application.use_cases.apply_playlist_assignments import (
    ApplyPlaylistAssignmentsCommand,
    ApplyPlaylistAssignmentsUseCase,
    run_apply_playlist_assignments,
)
from src.application.use_cases.create_playlist_assignment import (
    CreatePlaylistAssignmentCommand,
    CreatePlaylistAssignmentUseCase,
)
from src.application.use_cases.delete_playlist_assignment import (
    DeletePlaylistAssignmentCommand,
    DeletePlaylistAssignmentUseCase,
)
from src.domain.repositories import UnitOfWorkProtocol
from src.interface.api.deps import get_current_user_id
from src.interface.api.schemas.playlist_assignments import (
    ApplyResultSchema,
    AssignmentSchema,
    CreateAssignmentRequest,
    CreateAssignmentResponse,
)

router = APIRouter(prefix="/playlist-assignments", tags=["playlist-assignments"])


@router.post("", status_code=201)
async def create_and_apply_assignment(
    body: CreateAssignmentRequest,
    user_id: str = Depends(get_current_user_id),
) -> CreateAssignmentResponse:
    """Create an assignment and apply it to the playlist's tracks.

    Create and apply share one session to halve connection churn. The create
    leg commits first; if the apply leg errors, the assignment persists and
    can be retried via ``POST /{id}/apply``.
    """
    create_cmd = CreatePlaylistAssignmentCommand(
        user_id=user_id,
        connector_playlist_id=body.connector_playlist_id,
        action_type=body.action_type,
        raw_action_value=body.action_value,
    )

    async def _create_and_apply(uow: UnitOfWorkProtocol):
        create_result = await CreatePlaylistAssignmentUseCase().execute(create_cmd, uow)
        apply_cmd = ApplyPlaylistAssignmentsCommand(
            user_id=user_id,
            assignment_ids=[create_result.assignment.id],
        )
        apply_result = await ApplyPlaylistAssignmentsUseCase().execute(apply_cmd, uow)
        return create_result.assignment, apply_result

    assignment, apply_result = await execute_use_case(
        _create_and_apply, user_id=user_id
    )
    return CreateAssignmentResponse(
        assignment=AssignmentSchema.model_validate(assignment),
        result=ApplyResultSchema.model_validate(apply_result),
    )


@router.post("/{assignment_id}/apply")
async def apply_assignment(
    assignment_id: UUID,
    user_id: str = Depends(get_current_user_id),
) -> ApplyResultSchema:
    """Re-run the engine for a single existing assignment."""
    apply_result = await run_apply_playlist_assignments(
        user_id=user_id,
        assignment_ids=[assignment_id],
    )
    return ApplyResultSchema.model_validate(apply_result)


@router.delete("/{assignment_id}", status_code=204)
async def delete_assignment(
    assignment_id: UUID,
    user_id: str = Depends(get_current_user_id),
) -> None:
    """Remove an assignment. Already-applied tags/preferences are preserved."""
    cmd = DeletePlaylistAssignmentCommand(user_id=user_id, assignment_id=assignment_id)
    result = await execute_use_case(
        lambda uow: DeletePlaylistAssignmentUseCase().execute(cmd, uow),
        user_id=user_id,
    )
    if not result.deleted:
        raise HTTPException(status_code=404, detail="Assignment not found")
