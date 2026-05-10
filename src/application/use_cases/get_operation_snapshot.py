"""Snapshot lookup for an in-flight or completed workflow operation.

Returns persisted state from ``workflow_runs`` + ``workflow_run_nodes``
keyed by SSE ``operation_id``. Used by the frontend's REST polling
fallback when the SSE stream looks stalled (45 s without any frame),
and as the source of truth when the heartbeat sweeper marks a run
``failed`` for runs whose terminal SSE event was lost.

Authorization is checked through the workflow lookup chain — fetching
``workflow_by_id`` with the calling user_id raises NotFoundError if
the user doesn't own the workflow that produced this run.
"""

from attrs import define

from src.domain.entities.workflow import WorkflowRun
from src.domain.exceptions import NotFoundError
from src.domain.repositories import UnitOfWorkProtocol


@define(frozen=True, slots=True)
class GetOperationSnapshotCommand:
    user_id: str
    operation_id: str


@define(frozen=True, slots=True)
class GetOperationSnapshotResult:
    run: WorkflowRun


@define(slots=True)
class GetOperationSnapshotUseCase:
    """Resolve an SSE operation_id to its run snapshot.

    Two-step authorization:
      1. Run repository lookup by operation_id (NotFoundError if absent).
      2. Workflow lookup by workflow_id + user_id (NotFoundError if user
         doesn't own the workflow). The workflow check protects against
         operation_id being guessable.
    """

    async def execute(
        self, command: GetOperationSnapshotCommand, uow: UnitOfWorkProtocol
    ) -> GetOperationSnapshotResult:
        async with uow:
            run_repo = uow.get_workflow_run_repository()
            run = await run_repo.get_run_by_operation_id(command.operation_id)
            if run is None:
                raise NotFoundError(f"Operation {command.operation_id} not found")

            workflow_repo = uow.get_workflow_repository()
            # Raises NotFoundError if user_id doesn't own this workflow.
            _workflow = await workflow_repo.get_workflow_by_id(
                run.workflow_id, user_id=command.user_id
            )

            return GetOperationSnapshotResult(run=run)
