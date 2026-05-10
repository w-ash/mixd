"""Stale workflow-run sweeper — marks orphaned ``running`` rows as ``failed``.

Production runs can stall in three ways: (a) the orchestration runtime fails
to begin task execution (Prefect ephemeral cold-start hang on a small VM),
(b) the background coroutine is killed mid-flight (SIGINT during a deploy),
(c) the event loop is blocked long enough that the heartbeat ticker can't
fire. In all three the run row stays in ``status='running'`` indefinitely
because the only writers are the runtime itself.

A periodic sweeper running on the API process inspects rows whose
``heartbeat_at`` is older than a threshold and marks them ``failed`` with a
diagnostic ``error_message``. Runs without any heartbeat (``heartbeat_at IS
NULL``) past the threshold get ``cold-start hang``; runs that ticked at
least once get ``watchdog: heartbeat went silent``. The first tick after
restart also resolves runs orphaned by prior process kills.
"""

import asyncio
from datetime import UTC, datetime
from typing import Final

from src.application.runner import execute_use_case
from src.config.constants import WorkflowConstants
from src.config.logging import get_logger
from src.domain.entities.workflow import WorkflowRun
from src.domain.repositories.interfaces import UnitOfWorkProtocol

logger = get_logger(__name__).bind(service="workflow_run_sweeper")

# Sweeper cadence. Threshold is the inactivity window after which a run is
# considered stalled — see plan: 60s balances false-positive risk against
# user wait time on the failure surface.
SWEEP_INTERVAL_SECONDS: Final = 30
STALE_THRESHOLD_SECONDS: Final = 60


_COLD_START_MESSAGE: Final = (
    "cold-start hang: workflow runner did not begin task execution"
)
_WATCHDOG_MESSAGE: Final = "watchdog: heartbeat went silent"


def _classify_stall(run: WorkflowRun) -> str:
    return _COLD_START_MESSAGE if run.heartbeat_at is None else _WATCHDOG_MESSAGE


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


async def sweep_stalled_runs(
    uow: UnitOfWorkProtocol,
    *,
    stale_threshold_seconds: int = STALE_THRESHOLD_SECONDS,
) -> int:
    """Mark stalled ``running`` runs as ``failed`` in a single pass.

    Returns the number of rows transitioned. Idempotent — only touches rows
    that match the staleness condition. Safe to run on a schedule.
    """
    failed_count = 0
    async with uow:
        repo = uow.get_workflow_run_repository()
        stalled = await repo.list_stalled_runs(
            stale_threshold_seconds=stale_threshold_seconds
        )
        if not stalled:
            return 0

        now = datetime.now(UTC)
        for run in stalled:
            duration_ms: int | None = None
            if run.started_at is not None:
                duration_ms = int((now - run.started_at).total_seconds() * 1000)
            reason = _classify_stall(run)

            try:
                await repo.update_run_status(
                    run.id,
                    WorkflowConstants.RUN_STATUS_FAILED,
                    completed_at=now,
                    duration_ms=duration_ms,
                    error_message=reason,
                )
                failed_count += 1
                logger.warning(
                    "Marked stalled run as failed",
                    run_id=str(run.id),
                    workflow_id=str(run.workflow_id),
                    reason=reason,
                    started_at=_iso(run.started_at),
                    heartbeat_at=_iso(run.heartbeat_at),
                )
            except Exception:
                logger.warning(
                    "Failed to mark stalled run",
                    run_id=str(run.id),
                    exc_info=True,
                )

    if stalled and failed_count == 0:
        # All sweep writes failed — likely a connectivity or auth problem the
        # operator needs to see, since the per-tick logger only fires on success.
        logger.error(
            "Sweeper tick wrote no rows despite finding stalled runs",
            stalled_count=len(stalled),
        )

    return failed_count


async def run_sweeper_loop(
    *,
    interval_seconds: int = SWEEP_INTERVAL_SECONDS,
    stale_threshold_seconds: int = STALE_THRESHOLD_SECONDS,
) -> None:
    """Lifespan-managed loop. Sweeps every ``interval_seconds`` until cancelled.

    Each tick opens a short-lived UoW via ``execute_use_case``. Errors inside a
    tick are logged and swallowed so a transient failure (e.g. Neon cold-pause)
    doesn't kill the loop.
    """

    async def _tick(uow: UnitOfWorkProtocol) -> int:
        return await sweep_stalled_runs(
            uow, stale_threshold_seconds=stale_threshold_seconds
        )

    logger.info(
        "Workflow run sweeper started",
        interval_seconds=interval_seconds,
        stale_threshold_seconds=stale_threshold_seconds,
    )
    while True:
        try:
            count = await execute_use_case(_tick)
            if count > 0:
                logger.info("Sweeper tick", failed_count=count)
        except asyncio.CancelledError:
            logger.info("Workflow run sweeper cancelled")
            raise
        except Exception:
            logger.warning("Sweeper tick failed", exc_info=True)
        await asyncio.sleep(interval_seconds)
