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
import pathlib
from typing import Final

from src.application.runner import execute_use_case
from src.config.constants import WorkflowConstants
from src.config.logging import get_logger
from src.domain.entities.workflow import WorkflowRun
from src.domain.repositories.interfaces import UnitOfWorkProtocol


def _read_rss_kb() -> int | None:
    """Read process RSS in KB from ``/proc/self/status``. Linux-only; returns
    ``None`` on macOS/dev. Used to attach a memory snapshot when the sweeper
    classifies a run as stalled, so we can correlate stalls with RSS pressure
    on the Fly machine.
    """
    try:
        with pathlib.Path("/proc/self/status").open(encoding="utf-8") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])
    except OSError, ValueError:
        return None
    return None


logger = get_logger(__name__).bind(service="workflow_run_sweeper")

# Sweeper cadence. The poll interval is the loop's wake-up rhythm; the stale
# threshold is the inactivity window after which a run is reaped. The threshold
# is derived as a multiple of the heartbeat interval (not a hardcoded 60s) so
# the two can never drift apart — tightening the heartbeat tightens the reaper.
SWEEP_INTERVAL_SECONDS: Final = 30
STALE_THRESHOLD_SECONDS: Final = (
    WorkflowConstants.HEARTBEAT_INTERVAL_SECONDS
    * WorkflowConstants.HEARTBEAT_STALE_MULTIPLE
)


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
    """Mark stalled ``running`` runs as ``crashed`` in a single pass.

    A stalled run means the worker died or its event loop blocked — an
    operational event — so it is recorded ``crashed``, not ``failed`` (which is
    reserved for the workflow's own logic raising). Returns the number of rows
    transitioned. Idempotent — only touches rows that match the staleness
    condition, capped at ``SWEEP_MAX_BATCH`` per cycle. Safe to run on a
    schedule.
    """
    failed_count = 0
    async with uow:
        repo = uow.get_workflow_run_repository()
        stalled = await repo.list_stalled_runs(
            stale_threshold_seconds=stale_threshold_seconds,
            limit=WorkflowConstants.SWEEP_MAX_BATCH,
        )
        if not stalled:
            return 0

        now = datetime.now(UTC)
        for run in stalled:
            duration_ms: int | None = None
            if run.started_at is not None:
                duration_ms = int((now - run.started_at).total_seconds() * 1000)
            reason = _classify_stall(run)

            # Snapshot live asyncio tasks + process RSS at the moment of the
            # kill — these answer "what was stuck?" and "was memory tight?"
            # for the next post-mortem.
            live_tasks = sorted(t.get_name() for t in asyncio.all_tasks())
            rss_kb = _read_rss_kb()

            try:
                await repo.update_run_status(
                    run.id,
                    WorkflowConstants.RUN_STATUS_CRASHED,
                    completed_at=now,
                    duration_ms=duration_ms,
                    error_message=reason,
                )
                failed_count += 1
                logger.warning(
                    "Marked stalled run as crashed",
                    run_id=str(run.id),
                    workflow_id=str(run.workflow_id),
                    reason=reason,
                    started_at=_iso(run.started_at),
                    heartbeat_at=_iso(run.heartbeat_at),
                    live_tasks=live_tasks,
                    rss_kb=rss_kb,
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
