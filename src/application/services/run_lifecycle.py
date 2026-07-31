"""Shared workflow-run lifecycle helpers for the CLI and API.

Concrete ``RunStatusUpdater`` / ``NodeStatusUpdater`` implementations (and the
heartbeat ticker) injected into ``ExecuteWorkflowRunUseCase``. Both interfaces
import these so the run lifecycle lives in exactly one place — the use case owns
the RUNNING→terminal state machine; these helpers are just the thin persistence
adapters it calls.

Each helper runs on its own short-lived UoW rather than the run's: the terminal
status write must land even when the workflow's own session has been torn down
by the error that caused it. ``rollback=False`` is part of that — see
``runner.execute_use_case``.
"""

from datetime import datetime
from typing import Unpack
from uuid import UUID

from src.application.runner import execute_use_case
from src.application.workflows.protocols import RunStatusKwargs
from src.config.logging import get_logger
from src.domain.entities.workflow import RunStatus

logger = get_logger(__name__)


async def update_run_status(
    run_id: UUID,
    status: RunStatus,
    **kwargs: Unpack[RunStatusKwargs],
) -> bool:
    """Concrete ``RunStatusUpdater``. Returns whether a row was transitioned."""
    return await execute_use_case(
        lambda uow: uow.get_workflow_run_repository().update_run_status(
            run_id, status, **kwargs
        ),
        rollback=False,
    )


async def update_node_status(
    run_id: UUID,
    node_id: str,
    status: RunStatus,
    *,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
    duration_ms: int | None = None,
    input_track_count: int | None = None,
    output_track_count: int | None = None,
    error_message: str | None = None,
    node_details: dict[str, object] | None = None,
) -> None:
    """Concrete ``NodeStatusUpdater``."""
    await execute_use_case(
        lambda uow: uow.get_workflow_run_repository().update_node_status(
            run_id,
            node_id,
            status,
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=duration_ms,
            input_track_count=input_track_count,
            output_track_count=output_track_count,
            error_message=error_message,
            node_details=node_details,
        ),
        rollback=False,
    )


async def bump_heartbeat(run_id: UUID) -> None:
    """Concrete ``HeartbeatBumper`` — one liveness bump on a run.

    Suppresses errors — heartbeats are advisory; a transient DB blip during a
    tick mustn't crash the workflow. The ticker that calls this on a cadence
    lives in ``ExecuteWorkflowRunUseCase``, so every driver of a run gets it;
    CPU-bound transform/combiner nodes are offloaded to a worker thread (see node
    factories), so the event loop stays responsive and it keeps firing even under
    heavy transforms.
    """
    try:
        await execute_use_case(
            lambda uow: uow.get_workflow_run_repository().bump_heartbeat(run_id),
            rollback=False,
        )
    except Exception:
        logger.warning("Heartbeat bump failed", run_id=str(run_id), exc_info=True)
