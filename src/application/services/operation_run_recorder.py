"""Recorder for the OperationRun audit log (v0.7.7).

Stateless module functions called from the SSE seam
(``src/interface/api/services/sse_operations.py``) to write one row per
long-running operation. Each function delegates to ``execute_use_case``
so the write happens in its own short-lived UoW transaction with the
``user_context`` ContextVar set for RLS enforcement.

Use cases that want to record per-item failures call
:func:`append_run_issue` directly with the run_id threaded through their
emitter wiring. v0.7.7 deliberately keeps the recorder out of the
``OperationBoundEmitter`` to avoid coupling the SSE event stream to the
audit log; future iterations can add an auto-tee if call sites grow.
"""

from datetime import UTC, datetime
from uuid import UUID

from src.application.runner import execute_use_case
from src.domain.entities.operation_run import OperationRun, OperationStatus
from src.domain.entities.shared import JsonDict
from src.domain.repositories import UnitOfWorkProtocol


async def start_run(
    *,
    user_id: str,
    operation_type: str,
) -> UUID:
    """Insert an ``OperationRun`` row at operation kickoff.

    Returns the new ``run_id`` so the route can pass it to
    :func:`run_sse_operation` for terminal finalization.
    """

    async def _create(uow: UnitOfWorkProtocol) -> UUID:
        run = OperationRun(
            user_id=user_id,
            operation_type=operation_type,
            started_at=datetime.now(UTC),
            status="running",
        )
        async with uow:
            repo = uow.get_operation_run_repository()
            created = await repo.create(run)
            await uow.commit()
            return created.id

    return await execute_use_case(_create, user_id=user_id)


async def finalize_run(
    run_id: UUID,
    *,
    user_id: str,
    status: OperationStatus,
    counts: JsonDict | None = None,
) -> None:
    """Set the terminal fields and merge ``counts`` at run end.

    Called from ``run_sse_operation`` on success (``status="complete"``)
    or exception (``status="error"``). Counts merge at the JSONB level so
    partial counts emitted during the run are preserved.
    """

    async def _update(uow: UnitOfWorkProtocol) -> None:
        async with uow:
            repo = uow.get_operation_run_repository()
            await repo.update_status(
                run_id,
                user_id=user_id,
                status=status,
                ended_at=datetime.now(UTC),
                counts=counts,
            )
            await uow.commit()

    await execute_use_case(_update, user_id=user_id)


async def append_run_issue(
    run_id: UUID,
    *,
    user_id: str,
    issue: JsonDict,
) -> None:
    """Append one issue dict to the run's JSONB ``issues`` array."""

    async def _append(uow: UnitOfWorkProtocol) -> None:
        async with uow:
            repo = uow.get_operation_run_repository()
            await repo.append_issue(run_id, user_id=user_id, issue=issue)
            await uow.commit()

    await execute_use_case(_append, user_id=user_id)


__all__ = ["append_run_issue", "finalize_run", "start_run"]
