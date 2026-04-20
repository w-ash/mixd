"""Delete a PlaylistAssignment.

Cascade on ``playlist_assignments.id`` removes the member snapshot
automatically; this use case deletes only the assignment row itself. Cached
preferences/tags written by past applies are NOT cleaned up here — users
who want to remove the effects of an assignment should re-run the apply
(which will clear assignment-sourced rows for the now-missing assignment
via the snapshot diff) OR delete the canonical metadata directly.
"""

from uuid import UUID

from attrs import define

from src.domain.repositories import UnitOfWorkProtocol


@define(frozen=True, slots=True)
class DeletePlaylistAssignmentCommand:
    user_id: str
    assignment_id: UUID


@define(frozen=True, slots=True)
class DeletePlaylistAssignmentResult:
    deleted: bool


@define(slots=True)
class DeletePlaylistAssignmentUseCase:
    async def execute(
        self,
        command: DeletePlaylistAssignmentCommand,
        uow: UnitOfWorkProtocol,
    ) -> DeletePlaylistAssignmentResult:
        async with uow:
            repo = uow.get_playlist_assignment_repository()
            deleted = await repo.delete_assignment(
                command.assignment_id, user_id=command.user_id
            )
            if deleted:
                await uow.commit()
            return DeletePlaylistAssignmentResult(deleted=deleted)
