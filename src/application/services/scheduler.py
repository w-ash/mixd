"""In-process workflow/sync scheduler — the v0.8.2 dispatch engine.

A lifespan-managed loop (peer of ``workflow_run_sweeper``) that, each tick:

1. **reaps** schedules whose claim has been held longer than the stuck-start
   timeout (a dispatch that died without recording an outcome) — reaps them as a
   SKIP and advances (a reaped claim is indistinguishable from a graceful drain,
   so it must not inflate the failure streak);
2. **finds** every user's due, enabled, unclaimed schedule (cross-tenant);
3. **dispatches** them concurrently under a semaphore, each in its OWN
   try/except so one failure can't cancel its siblings (the inverse of the
   workflow level-executor, which fails the group on any error).

Per dispatch: optimistically **claim** the row (only one poller, even across
machines, wins), run it under the schedule owner's RLS + OAuth identity, then
record the outcome and advance ``next_run_at``. Workflow runs and sync operations
both write an audit row carrying ``triggered_by_schedule_id`` so a scheduled run
traces back to its trigger.

Outcome classification: a dispatch is classified ONCE into a ``_Disposition``
(``success`` / ``skip`` / ``failure``) where both failure signals are visible — a
raised exception AND a returned result that reports errors without raising — so
``_process_one`` routes on one decision and never re-reads a status string. The
terminal write recomputes ``next_run_at`` from a FRESH read so a cadence the user
edited mid-dispatch is not clobbered by one derived from the captured row.

Catchup policy (``settings.scheduler.catchup``): a due fire whose window was
*missed* (more than a grace period late, e.g. after downtime) is, by default,
**skipped and advanced** — no backfill — rather than fired. ``catchup=True`` runs
one catchup fire instead.

Dispatch timeouts — two distinct bounds:

- ``dispatch_timeout_seconds`` is the LIVE-cancellation bound. Each dispatch runs
  under ``asyncio.timeout``; an overrun cancels the coroutine (freeing its
  semaphore slot) and is recorded as a ``failure``. Without it, one wedged
  dispatch would stall the whole tick (the TaskGroup awaits every dispatch).
- ``stuck_start_timeout_seconds`` is the DB-side reaper's bound for a *dead*
  dispatch (process killed before recording an outcome). It is longer than the
  live bound, and recovers the row as a skip — it does not cancel a live coroutine.

A shutdown ``CancelledError`` (external, not the timeout's) releases the claim as
a skip and propagates, so structured cancellation still tears the loop down.
"""

import asyncio
from asyncio import CancelledError
import contextlib
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from attrs import define

from src.application.runner import execute_use_case
from src.application.services.operation_run_recorder import finalize_run, start_run
from src.application.services.periodic_loop import run_periodic_background_loop
from src.application.services.schedule_timing import compute_next_run
from src.application.use_cases._shared.sync_targets import (
    SYNC_DISPATCH,
    sync_result_failed,
)
from src.application.use_cases.workflow_runs import (
    ExecuteWorkflowRunUseCase,
    RunWorkflowCommand,
    RunWorkflowUseCase,
)
from src.application.workflows.protocols import NodeStatusUpdater, RunStatusUpdater
from src.config import settings
from src.config.constants import WorkflowConstants
from src.config.logging import get_logger
from src.domain.entities.schedule import Schedule
from src.domain.exceptions import NotFoundError, WorkflowAlreadyRunningError
from src.domain.repositories.interfaces import UnitOfWorkProtocol

logger = get_logger(__name__).bind(service="workflow_scheduler")

# How a completed dispatch maps onto the schedule's next state. Classified ONCE,
# at dispatch time, where both failure signals are visible — a raised exception
# AND a returned result that reports errors without raising. _process_one then
# routes purely on this, with no status-string knowledge of its own.
#   success — the fire ran and ended cleanly → reset the failure streak.
#   skip    — not the schedule's fault (cancelled drain) → advance, no streak change.
#   failure — the fire ran but ended in error → bump the failure streak.
type _Disposition = Literal["success", "skip", "failure"]


@define(frozen=True, slots=True)
class _DispatchOutcome:
    """What a single dispatch produced, for the post-run schedule write."""

    run_id: UUID | None
    disposition: _Disposition
    # Recorded into schedules.last_run_status (the run's terminal status, or a
    # coarse label like "completed" for syncs).
    status: str
    # Leak-safe summary for schedules.last_error; set only for a `failure`.
    error_label: str | None = None


class _UnschedulableTargetError(Exception):
    """A schedule names a sync target no longer present in ``SYNC_DISPATCH``.

    Raised by ``_dispatch_sync`` and handled by ``_process_one`` as an auto-disable
    (a maintenance event — a connector was removed while a schedule for it still
    exists), NOT as a per-tick failure that would re-fire and re-fail forever.
    """

    def __init__(self, target: str) -> None:
        self.target = target
        super().__init__(f"unschedulable sync target {target!r}")


def _classify_run_status(status: str) -> _Disposition:
    """Map a workflow run's terminal status to a schedule disposition.

    Fail-class (failed/crashed) is a fault; a ``cancelled`` run is an operational
    drain (deploy/autoscale), not the schedule's fault, so it advances as a skip
    without touching the failure streak; anything else is a success.
    """
    if status in WorkflowConstants.RUN_STATUSES_FAIL_CLASS:
        return "failure"
    if status == WorkflowConstants.RUN_STATUS_CANCELLED:
        return "skip"
    return "success"


def _safe_failure_message(exc: Exception) -> str:
    """A leak-safe failure summary for ``schedules.last_error``.

    Deliberately the exception CLASS name, never ``str(exc)``: a connector error
    can embed an OAuth token or a signed URL in its message, and this value is
    surfaced in the UI failure banner. The class name (e.g. ``HTTPStatusError``,
    ``SpotifyAuthError``) is enough to triage; full detail lives in the per-run
    audit row that ``triggered_by_schedule_id`` links back to.
    """
    return type(exc).__name__


# ---------------------------------------------------------------------------
# Per-target dispatch
# ---------------------------------------------------------------------------


async def _dispatch_workflow(
    schedule: Schedule,
    *,
    update_run_status: RunStatusUpdater,
    update_node_status: NodeStatusUpdater,
) -> _DispatchOutcome:
    """Create the pending run (under the owner's RLS) then drive it to terminal.

    Mirrors the CLI/API path exactly — ``RunWorkflowUseCase`` then
    ``ExecuteWorkflowRunUseCase`` — so the run lifecycle lives in one place.
    ``RunWorkflowUseCase`` trips ``WorkflowAlreadyRunningError`` if a run is
    already active; that propagates to the caller, which treats it as a skip.
    """
    user_id = schedule.user_id
    workflow_id = schedule.workflow_id
    if workflow_id is None:  # defensive — target_type guarantees this is set
        raise ValueError("workflow schedule has no workflow_id")

    # Allocate a bare operation_id handle (no in-memory SSE queue — that lives in
    # the web process and would never be consumed here). Persisting it lets the
    # web UI reconnect to a scheduled run via the snapshot endpoint, which reads
    # run + node state purely from the DB. Live SSE is unavailable for scheduled
    # runs; the frontend's snapshot-poll fallback covers them.
    operation_id = str(uuid4())
    run_result = await execute_use_case(
        lambda uow: RunWorkflowUseCase().execute(
            RunWorkflowCommand(
                user_id=user_id,
                workflow_id=workflow_id,
                triggered_by_schedule_id=schedule.id,
                operation_id=operation_id,
            ),
            uow,
        ),
        user_id=user_id,
    )

    exec_result = await ExecuteWorkflowRunUseCase(
        update_run_status=update_run_status,
        update_node_status=update_node_status,
    ).execute(run_result.workflow.definition, run_result.run_id, user_id=user_id)
    status = exec_result.status
    disposition = _classify_run_status(status)
    return _DispatchOutcome(
        run_id=exec_result.run_id,
        disposition=disposition,
        status=status,
        error_label=f"run ended: {status}" if disposition == "failure" else None,
    )


async def _dispatch_sync(schedule: Schedule) -> _DispatchOutcome:
    """Run a background sync, wrapped in an OperationRun audit row.

    Unlike the SSE path, nothing upstream records the audit row for a scheduled
    sync — so the scheduler opens it here (carrying ``triggered_by_schedule_id``)
    and finalizes it.

    Two failure signals are read here, not just one:

    - a **raised** exception (hard failure) → finalize the audit row ``error`` and
      return a ``failure`` outcome (no re-raise — the caller routes on disposition);
    - a **returned** ``OperationResult`` that reports errors WITHOUT raising (the
      sync use cases catch and return on a handled failure) → also a ``failure``.

    The schedule outcome reflects the sync itself: the audit-row ``finalize_run``
    is best-effort (``contextlib.suppress``) so a transient audit-write error can't
    flip a successful sync into a recorded schedule failure (and a dangling run).
    A ``CancelledError`` (shutdown mid-sync) is BaseException, so it is not caught
    here — it propagates to ``_process_one``, which releases the claim as a skip.
    """
    user_id = schedule.user_id
    target = schedule.sync_target
    # Resolve the dispatch BEFORE opening the audit row, so an unknown target
    # never leaves a dangling running OperationRun. An unknown target is a
    # maintenance event (a connector removed from SYNC_DISPATCH while a schedule
    # for it still exists), handled by _process_one as an auto-disable — not a
    # per-tick failure. SYNC_DISPATCH is the single source of truth.
    run_sync = SYNC_DISPATCH.get(target or "")
    if run_sync is None:
        raise _UnschedulableTargetError(target or "")

    op_run_id = await start_run(
        user_id=user_id,
        operation_type=f"scheduled_sync:{target}",
        triggered_by_schedule_id=schedule.id,
    )
    try:
        op_result = await run_sync(user_id)
    except Exception as exc:
        with contextlib.suppress(Exception):
            await finalize_run(op_run_id, user_id=user_id, status="error")
        return _DispatchOutcome(
            run_id=op_run_id,
            disposition="failure",
            status="failed",
            error_label=_safe_failure_message(exc),
        )

    failed = sync_result_failed(op_result)
    with contextlib.suppress(Exception):
        await finalize_run(
            op_run_id, user_id=user_id, status="error" if failed else "complete"
        )
    return _DispatchOutcome(
        run_id=op_run_id,
        disposition="failure" if failed else "success",
        status="failed" if failed else "completed",
        error_label="sync reported errors" if failed else None,
    )


# ---------------------------------------------------------------------------
# Schedule-state writes (cross-tenant, each in its own short transaction)
# ---------------------------------------------------------------------------


async def _claim(schedule: Schedule, now: datetime) -> bool:
    # next_run_at is non-null for any due row; narrow to a local for the closure.
    expected = schedule.next_run_at
    if expected is None:
        return False

    async def _op(uow: UnitOfWorkProtocol) -> bool:
        async with uow:
            return await uow.get_schedule_repository().mark_schedule_started(
                schedule.id, expected_next_run_at=expected, now=now
            )

    return await execute_use_case(_op)


async def _release(
    schedule: Schedule,
    *,
    disposition: _Disposition,
    now: datetime,
    last_run_status: str,
    last_run_id: UUID | None = None,
    last_error: str | None = None,
    reset_failures: bool = False,
) -> None:
    """Release the claim and advance one schedule, routed by ``disposition``.

    ``next_run_at`` is recomputed from the schedule's CURRENT cadence, re-read in
    the SAME transaction as the advance-write. A user can edit cadence
    (``update_schedule``) while a dispatch is in flight: that edit writes a new
    ``next_run_at`` but cannot clear the claim. Reading fresh here — instead of
    advancing from the row captured at tick start — means every exit path honors
    the user's NEW cadence rather than clobbering it. Falls back to the captured
    entity if the row vanished. One transaction over the repo's already-deduped
    ``_release_and_advance``, with the success/skip/failure decision in one ``match``.
    """

    async def _op(uow: UnitOfWorkProtocol) -> None:
        async with uow:
            repo = uow.get_schedule_repository()
            try:
                fresh = await repo.get_by_id(schedule.id)
            except NotFoundError:
                fresh = schedule
            next_run_at = compute_next_run(fresh, now=now)
            match disposition:
                case "success":
                    await repo.mark_schedule_completed(
                        schedule.id,
                        next_run_at=next_run_at,
                        last_run_at=now,
                        last_run_status=last_run_status,
                        last_run_id=last_run_id,
                    )
                case "failure":
                    await repo.mark_schedule_failed(
                        schedule.id,
                        next_run_at=next_run_at,
                        last_run_at=now,
                        last_error=last_error or "unknown error",
                        last_run_status=last_run_status,
                    )
                case "skip":
                    await repo.mark_schedule_skipped(
                        schedule.id,
                        next_run_at=next_run_at,
                        last_run_at=now,
                        last_run_status=last_run_status,
                        reset_failures=reset_failures,
                    )

    await execute_use_case(_op)


async def _disable(schedule_id: UUID, *, last_error: str) -> None:
    """Disable a schedule and release its claim (unschedulable target).

    The poll filters ``status='enabled'``, so a disabled schedule stops re-firing
    — turning a forever-failing orphaned target into a one-time, surfaced event.
    """

    async def _op(uow: UnitOfWorkProtocol) -> None:
        async with uow:
            await uow.get_schedule_repository().mark_schedule_disabled(
                schedule_id, last_error=last_error
            )

    await execute_use_case(_op)


# ---------------------------------------------------------------------------
# One schedule, end to end
# ---------------------------------------------------------------------------


async def _process_one(
    schedule: Schedule,
    *,
    now: datetime,
    update_run_status: RunStatusUpdater,
    update_node_status: NodeStatusUpdater,
    catchup: bool,
    grace_seconds: int,
) -> None:
    """Claim, dispatch, and record the outcome for a single due schedule.

    Claiming first means a lost race (another poller won, or the row is no
    longer due) is a cheap no-op. After a win, every exit path advances
    ``next_run_at`` and releases the claim, so the row can never get stuck
    enabled-but-unclaimable.

    ``now`` is the tick's reference time (when the row was found due), NOT a
    fresh read taken after the per-dispatch semaphore wait — otherwise a schedule
    that merely queued behind slow siblings would look "missed". Actual
    completion times below use fresh ``datetime.now(UTC)``.
    """
    claimed = await _claim(schedule, now)
    if not claimed:
        logger.debug("schedule claim lost", schedule_id=str(schedule.id))
        return

    # Missed-window policy. next_run_at is non-null on a due row (the claim
    # guarded on it), so the type narrow below is just for the checker. Computed
    # from the captured entity: the miss decision and this write both happen
    # before dispatch, so the cadence-edit race window does not apply.
    due_at = schedule.next_run_at or now
    missed = (now - due_at).total_seconds() > grace_seconds
    if missed and not catchup:
        logger.info(
            "schedule window missed — advancing without backfill",
            schedule_id=str(schedule.id),
        )
        await _release(
            schedule,
            disposition="skip",
            now=now,
            last_run_status="skipped_missed",
        )
        return

    try:
        if schedule.target_type == "workflow":
            outcome = await _dispatch_workflow(
                schedule,
                update_run_status=update_run_status,
                update_node_status=update_node_status,
            )
        else:
            outcome = await _dispatch_sync(schedule)
    except WorkflowAlreadyRunningError:
        # A run already holds the slot — not a fault, and proof the workflow is
        # healthy right now, so this skip RESETS the failure streak.
        skipped_at = datetime.now(UTC)
        await _release(
            schedule,
            disposition="skip",
            now=skipped_at,
            last_run_status="skipped_already_running",
            reset_failures=True,
        )
        return
    except _UnschedulableTargetError as exc:
        # Orphaned target (a connector removed from SYNC_DISPATCH while a schedule
        # for it remains). Disable instead of failing every tick forever.
        logger.warning(
            "scheduled sync target unschedulable — disabling schedule",
            schedule_id=str(schedule.id),
            target=exc.target,
        )
        await _disable(schedule.id, last_error="unschedulable target")
        return
    except Exception as exc:
        # A claim/mark write or other unexpected error reached here (dispatch
        # itself now returns a failure disposition rather than raising). Record a
        # failure with a leak-safe label and advance from the fresh cadence.
        logger.error(
            "scheduled dispatch raised",
            schedule_id=str(schedule.id),
            exc_info=True,
        )
        failed_at = datetime.now(UTC)
        await _release(
            schedule,
            disposition="failure",
            now=failed_at,
            last_run_status="failed",
            last_error=_safe_failure_message(exc),
        )
        return

    # Terminal write, routed purely by the classified disposition. _release
    # recomputes next_run_at from a fresh read so a cadence edited mid-dispatch
    # is not clobbered.
    finished_at = datetime.now(UTC)
    await _release(
        schedule,
        disposition=outcome.disposition,
        now=finished_at,
        last_run_status=outcome.status,
        last_run_id=outcome.run_id,
        last_error=outcome.error_label,
    )


# ---------------------------------------------------------------------------
# Tick + loop
# ---------------------------------------------------------------------------


async def run_scheduler_tick(
    uow: UnitOfWorkProtocol,
    *,
    now: datetime,
    update_run_status: RunStatusUpdater,
    update_node_status: NodeStatusUpdater,
    max_concurrent: int,
    stuck_timeout_seconds: int,
    dispatch_timeout_seconds: int,
    catchup: bool,
    grace_seconds: int,
) -> int:
    """One scheduler pass: reap stuck claims, then dispatch all due schedules.

    Returns the number of due schedules considered this tick. The reaper and the
    due-poll share the passed ``uow`` (one short read/write transaction); each
    dispatch then runs against its own sessions so a long run never holds the
    poll transaction open. Both reads are bounded by the repository's own
    ``DUE_BATCH_MAX`` cap, so a large backlog can't make one tick unbounded.
    """
    async with uow:
        repo = uow.get_schedule_repository()
        # Per-tick leader election: only the instance that wins the transaction-
        # level advisory lock scans/reaps this tick, so N replicas don't each run
        # N redundant cross-tenant scans. The lock auto-releases when this poll
        # transaction ends; double-dispatch is prevented independently by the
        # mark_schedule_started claim. Losing the lock just skips this tick's scan.
        if not await repo.try_acquire_poll_lock():
            logger.debug("scheduler poll lock held elsewhere — skipping tick")
            return 0
        stuck = await repo.list_stuck_started(stuck_timeout_seconds, now=now)
        for s in stuck:
            # A reaped claim is indistinguishable from a graceful drain (deploy
            # killed the dispatch before it recorded an outcome), so reap it as a
            # SKIP — no failure-streak bump. A genuinely hung dispatch is already
            # recorded as a real failure by the per-dispatch timeout, which fires
            # well before the (longer) stuck-start timeout the reaper waits on.
            await repo.mark_schedule_skipped(
                s.id,
                next_run_at=compute_next_run(s, now=now),
                last_run_at=now,
                last_run_status="reaped",
            )
        if stuck:
            logger.warning("reaped stuck schedules", count=len(stuck))
        due = await repo.find_due_schedules(now)

    if not due:
        return 0

    semaphore = asyncio.Semaphore(max_concurrent)

    async def _guarded(schedule: Schedule) -> None:
        async with semaphore:
            try:
                # Per-dispatch live-cancellation bound: a connector that hangs
                # (missing timeout, unbounded retry, DB lock wait) must not stall
                # the whole tick — the TaskGroup awaits every dispatch, so one
                # wedged coroutine would freeze all scheduling. asyncio.timeout
                # cancels the overrunning dispatch and surfaces as TimeoutError;
                # an EXTERNAL cancellation (shutdown) propagates as CancelledError
                # instead, which is how the two cases are told apart here.
                async with asyncio.timeout(dispatch_timeout_seconds):
                    await _process_one(
                        schedule,
                        now=now,
                        update_run_status=update_run_status,
                        update_node_status=update_node_status,
                        catchup=catchup,
                        grace_seconds=grace_seconds,
                    )
            except TimeoutError:
                # The dispatch overran the bound; its coroutine is already
                # cancelled and its slot freed. A hung dispatch IS a fault → record
                # a failure and advance (best-effort write).
                logger.error(
                    "scheduled dispatch timed out",
                    schedule_id=str(schedule.id),
                    timeout_seconds=dispatch_timeout_seconds,
                )
                with contextlib.suppress(Exception):
                    timed_out_at = datetime.now(UTC)
                    await _release(
                        schedule,
                        disposition="failure",
                        now=timed_out_at,
                        last_run_status="timeout",
                        last_error="dispatch timed out",
                    )
            except CancelledError:
                # Graceful shutdown mid-dispatch (deploy/autoscale). Release the
                # claim as a skip so the stuck reaper doesn't later light the
                # failure banner on every deploy, then re-raise so structured
                # cancellation still tears the loop down. Best-effort — a write
                # during shutdown may itself fail (the reaper-as-skip backstops it).
                with contextlib.suppress(Exception):
                    shutdown_at = datetime.now(UTC)
                    await _release(
                        schedule,
                        disposition="skip",
                        now=shutdown_at,
                        last_run_status="skipped_shutdown",
                    )
                raise
            except Exception:
                # Backstop: _process_one already handles dispatch failures, so
                # reaching here means a claim/mark write itself failed. Isolate
                # it — a sibling's success must not be cancelled.
                logger.error(
                    "schedule processing crashed",
                    schedule_id=str(schedule.id),
                    exc_info=True,
                )

    async with asyncio.TaskGroup() as tg:
        for schedule in due:
            _ = tg.create_task(_guarded(schedule))

    return len(due)


async def run_scheduler_loop(
    *,
    update_run_status: RunStatusUpdater,
    update_node_status: NodeStatusUpdater,
) -> None:
    """Lifespan-managed scheduler loop. Polls until cancelled.

    The concrete run-lifecycle updaters are injected by the lifespan (interface
    layer) — they open their own sessions for run/node writes, exactly as the
    CLI and API inject them into ``ExecuteWorkflowRunUseCase``. Tuning comes from
    ``settings.scheduler``.

    Multi-instance safety lives one level down: each tick takes a transaction-level
    advisory lock (``try_acquire_poll_lock``) so only one replica scans per tick.
    Every replica runs this same loop unconditionally; no leader state is held here.
    """
    cfg = settings.scheduler
    # A window is "missed" once it's more than two poll intervals stale — long
    # enough that ordinary poll jitter never counts as a miss.
    grace_seconds = cfg.poll_interval_seconds * 2

    async def _tick(uow: UnitOfWorkProtocol) -> int:
        return await run_scheduler_tick(
            uow,
            now=datetime.now(UTC),
            update_run_status=update_run_status,
            update_node_status=update_node_status,
            max_concurrent=cfg.max_concurrent_scheduled_runs,
            stuck_timeout_seconds=cfg.stuck_start_timeout_seconds,
            dispatch_timeout_seconds=cfg.dispatch_timeout_seconds,
            catchup=cfg.catchup,
            grace_seconds=grace_seconds,
        )

    def _log(due_count: int) -> None:
        if due_count > 0:
            logger.info("scheduler tick", due_count=due_count)

    await run_periodic_background_loop(
        _tick,
        interval_seconds=cfg.poll_interval_seconds,
        name="workflow_scheduler",
        log_result=_log,
    )
