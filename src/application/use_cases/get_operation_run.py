"""Get one OperationRun by id, scoped to the requesting user (v0.7.7)."""

from uuid import UUID

from attrs import define

from src.domain.entities.operation_run import OperationRun
from src.domain.repositories import UnitOfWorkProtocol


@define(frozen=True, slots=True)
class GetOperationRunCommand:
    user_id: str
    run_id: UUID


@define(slots=True)
class GetOperationRunUseCase:
    async def execute(
        self,
        command: GetOperationRunCommand,
        uow: UnitOfWorkProtocol,
    ) -> OperationRun | None:
        """Return the run if owned by ``user_id``, else None.

        The route translates ``None`` into a 404 response (NOT 403) so the
        same code path covers both not-found and not-owner — avoiding the
        existence-leak the plan calls out.
        """
        async with uow:
            repo = uow.get_operation_run_repository()
            return await repo.get_by_id_for_user(
                command.run_id, user_id=command.user_id
            )
