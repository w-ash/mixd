"""Shared workflow-run lifecycle helpers for the CLI and API.

Concrete ``RunStatusUpdater`` / ``NodeStatusUpdater`` implementations (and the
heartbeat ticker) injected into ``ExecuteWorkflowRunUseCase``. Both interfaces
import these so the run lifecycle lives in exactly one place — the use case owns
the RUNNING→terminal state machine; these helpers are just the thin DB-session
adapters it calls. They legitimately import infrastructure for session/repo
wiring, which is why they sit in the interface layer rather than the application
layer.
"""

import asyncio
import contextlib
from datetime import datetime
from typing import Unpack
from uuid import UUID

from src.application.workflows.protocols import RunStatusKwargs
from src.config.constants import WorkflowConstants
from src.config.logging import get_logger
from src.domain.entities.workflow import RunStatus

logger = get_logger(__name__)


@contextlib.asynccontextmanager
async def run_repo_session():
    """Short-lived independent session for run/node status updates.

    Status updates use their own session (``rollback=False``) so they survive
    a workflow failure — the terminal write must land even when the run's own
    session has been torn down by the error. Commits on clean exit.
    """
    from src.infrastructure.persistence.database.db_connection import get_session
    from src.infrastructure.persistence.repositories.workflow.runs import (
        WorkflowRunRepository,
    )

    async with get_session(rollback=False) as session:
        yield WorkflowRunRepository(session)
        await session.commit()


async def update_run_status(
    run_id: UUID,
    status: RunStatus,
    **kwargs: Unpack[RunStatusKwargs],
) -> bool:
    """Concrete ``RunStatusUpdater``. Returns whether a row was transitioned."""
    async with run_repo_session() as repo:
        return await repo.update_run_status(run_id, status, **kwargs)


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
    async with run_repo_session() as repo:
        await repo.update_node_status(
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
        )


async def bump_heartbeat(run_id: UUID) -> None:
    """Bump ``heartbeat_at`` on a run so the sweeper sees liveness.

    Suppresses errors — heartbeats are advisory; a transient DB blip during a
    tick mustn't crash the workflow.
    """
    try:
        async with run_repo_session() as repo:
            await repo.bump_heartbeat(run_id)
    except Exception:
        logger.warning("Heartbeat bump failed", run_id=str(run_id), exc_info=True)


async def heartbeat_loop(
    run_id: UUID,
    *,
    interval_seconds: int = WorkflowConstants.HEARTBEAT_INTERVAL_SECONDS,
) -> None:
    """Periodic ticker bumping ``heartbeat_at`` while a workflow runs.

    Runs as an asyncio task. CPU-bound transform/combiner nodes are offloaded to
    a worker thread (see node factories), so the event loop stays responsive and
    this ticker keeps firing even under heavy transforms. Cancellation by the
    foreground task is the normal exit path; a missed bump (DB blip) just means
    the next tick catches up, well inside the sweeper's stale threshold
    (HEARTBEAT_INTERVAL_SECONDS x HEARTBEAT_STALE_MULTIPLE).
    """
    logger.info("Heartbeat first bump attempt", run_id=str(run_id))
    await bump_heartbeat(run_id)
    while True:
        await asyncio.sleep(interval_seconds)
        await bump_heartbeat(run_id)
