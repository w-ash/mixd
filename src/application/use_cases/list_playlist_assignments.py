"""List a user's PlaylistAssignments."""

from collections.abc import Sequence

from attrs import define

from src.domain.entities.playlist_assignment import PlaylistAssignment
from src.domain.repositories import UnitOfWorkProtocol


@define(frozen=True, slots=True)
class ListPlaylistAssignmentsCommand:
    user_id: str


@define(frozen=True, slots=True)
class ListPlaylistAssignmentsResult:
    assignments: Sequence[PlaylistAssignment]


@define(slots=True)
class ListPlaylistAssignmentsUseCase:
    async def execute(
        self,
        command: ListPlaylistAssignmentsCommand,
        uow: UnitOfWorkProtocol,
    ) -> ListPlaylistAssignmentsResult:
        async with uow:
            repo = uow.get_playlist_assignment_repository()
            assignments = await repo.list_for_user(user_id=command.user_id)
            return ListPlaylistAssignmentsResult(assignments=assignments)


async def run_list_playlist_assignments(
    user_id: str,
) -> ListPlaylistAssignmentsResult:
    """Convenience wrapper for CLI / API handlers."""
    from src.application.runner import execute_use_case

    command = ListPlaylistAssignmentsCommand(user_id=user_id)
    return await execute_use_case(
        lambda uow: ListPlaylistAssignmentsUseCase().execute(command, uow),
        user_id=user_id,
    )
