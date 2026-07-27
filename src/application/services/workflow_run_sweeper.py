"""Stale workflow-run sweeper — marks orphaned ``running`` rows as ``crashed``.

Production runs can stall in three ways: (a) the execution coroutine fails
before recording a terminal state (e.g. a wedged connector the per-node
timeout can't interrupt), (b) the background coroutine is killed mid-flight
(SIGINT during a deploy),
(c) the event loop is blocked long enough that the heartbeat ticker can't
fire. In all three the run row stays in ``status='running'`` indefinitely
because the only writers are the runtime itself.

A periodic sweeper running on the API process inspects rows whose
``heartbeat_at`` is older than a threshold and marks them ``failed`` with a
diagnostic ``error_message``. Runs without any heartbeat (``heartbeat_at IS
NULL``) past the threshold get ``cold-start hang``; runs that ticked at
least once get ``watchdog: heartbeat went silent``. The first tick after
restart also resolves runs orphaned by prior process kills.

The sweep is **paced by activity, not a fixed clock**. The condition it looks for
can only arise while a run is in flight or after a process death — and the
startup tick already covers the latter — so ticking every 30s unconditionally
bought nothing and kept Neon's compute permanently awake (scale-to-zero needs 5
minutes with no query activity). The loop now sweeps at the active cadence only
while ``run_activity`` reports runs executing locally, and falls back to a long
idle interval otherwise.
"""

import asyncio
from datetime import UTC, datetime
import pathlib
from typing import Final

from src.application.services.periodic_loop import run_adaptive_background_loop
from src.application.services.run_activity import activity_event, runs_in_flight
from src.config.constants import WorkflowConstants
from src.config.logging import get_logger
from src.domain.entities.workflow import WorkflowRun
from src.domain.repositories.uow import UnitOfWorkProtocol


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

# Sweeper cadence. The stale threshold is the inactivity window after which a run
# is reaped. It is derived as a multiple of the heartbeat interval (not a
# hardcoded 60s) so the two can never drift apart — tightening the heartbeat
# tightens the reaper.
#
# Two intervals, because the sweep is only useful when there is something to
# sweep. ``SWEEP_INTERVAL_SECONDS`` is the *active* rhythm, used while runs
# execute in this process. ``IDLE_SWEEP_INTERVAL_SECONDS`` is the fallback used
# otherwise: long enough that Neon's compute suspends between ticks (a query
# every T keeps a 5-minute-timeout compute awake min(5min, T) out of every T, so
# 6h ≈ 1.4% duty cycle vs. 100% for the old fixed 30s), but still bounded so a
# run orphaned by *another* process — which the in-process activity counter
# cannot see — is eventually reaped rather than stranded until the next restart.
SWEEP_INTERVAL_SECONDS: Final = 30
IDLE_SWEEP_INTERVAL_SECONDS: Final = 6 * 60 * 60
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

        async def _mark_run_crashed(
            run: WorkflowRun,
            duration_ms: int | None,
            reason: str,
            live_tasks: list[str],
            rss_kb: int | None,
        ) -> int:
            """Mark one stalled run crashed; return 1 if counted, else 0.

            Holds the DB write and success logging so the caller's protective
            ``try``/``except Exception`` stays narrow while still covering every
            statement that can raise (the update and the structured logs).
            """
            transitioned = await repo.update_run_status(
                run.id,
                WorkflowConstants.RUN_STATUS_CRASHED,
                completed_at=now,
                duration_ms=duration_ms,
                error_message=reason,
            )
            if not transitioned:
                # The run reached a terminal state between list_stalled_runs
                # and this write (its own completion path won the race), so
                # the guarded UPDATE no-op'd. Don't count it as a crash.
                logger.info(
                    "Stalled run already terminal — skipped",
                    run_id=str(run.id),
                    workflow_id=str(run.workflow_id),
                )
                return 0
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
            return 1

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
                failed_count += await _mark_run_crashed(
                    run, duration_ms, reason, live_tasks, rss_kb
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
    idle_interval_seconds: int = IDLE_SWEEP_INTERVAL_SECONDS,
    stale_threshold_seconds: int = STALE_THRESHOLD_SECONDS,
) -> None:
    """Lifespan-managed loop. Sweeps until cancelled, pacing itself by activity.

    Sweeps every ``interval_seconds`` while runs are executing in this process,
    and only every ``idle_interval_seconds`` otherwise — a starting run sets the
    activity event, which cuts the idle sleep short. The first tick still runs
    immediately on startup, which is what recovers runs orphaned by a prior
    process kill.

    A thin binding over the shared ``run_adaptive_background_loop`` skeleton —
    the sleep, transient-error swallowing, and clean cancellation all live there.
    """

    async def _tick(uow: UnitOfWorkProtocol) -> int:
        return await sweep_stalled_runs(
            uow, stale_threshold_seconds=stale_threshold_seconds
        )

    def _log(count: int) -> None:
        if count > 0:
            logger.info("Sweeper tick", failed_count=count)

    def _next_delay(_count: int) -> float:
        return interval_seconds if runs_in_flight() > 0 else idle_interval_seconds

    logger.info(
        "Workflow run sweeper config",
        interval_seconds=interval_seconds,
        idle_interval_seconds=idle_interval_seconds,
        stale_threshold_seconds=stale_threshold_seconds,
    )
    await run_adaptive_background_loop(
        _tick,
        next_delay=_next_delay,
        name="workflow_run_sweeper",
        error_delay_seconds=interval_seconds,
        wake_event=activity_event(),
        log_result=_log,
    )
