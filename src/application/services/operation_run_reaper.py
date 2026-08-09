"""Startup reaper for ``operation_runs`` rows orphaned by an unobserved death.

v0.10.2.5's shutdown hook finalizes runs whose cancellation the process
*observes* (SIGTERM → drain → shielded audit write). A SIGKILL, an OOM kill,
or the machine going away leaves no chance to write anything, so the row stays
``running`` forever — the phantom the 2026-08-08 autostop incident produced.

The tick runs ONCE at startup, from the lifespan's ``startup_tasks``. That is
sufficient — not a compromise — while imports run on one machine: a row still
``running`` when this process boots was owned by the previous process, which
is gone. The heartbeat-column + periodic-sweeper shape (workflow runs have it
via ``workflow_run_sweeper.py`` on ``heartbeat_at``) is deliberately NOT
adopted here: it costs a schema change plus a per-run write cadence, and it
only earns that when a *live* process's runs must be watched for silent stalls
— revisit when unattended runs outgrow the startup-only guarantee (the
v0.10.2.8 backlog decision pins this to "when v0.10.2.6 ships" experience).

Also home to :func:`count_running_runs`, the DB half of the pre-deploy busy
gate (``GET /health?busy=true``): both consumers share the same process-level
view of in-flight ``running`` rows — the reaper through the row-fetching query
(it must finalize each row), the gate through a bare SQL count (it needs one
integer and must never miss a live run behind the reaper's batch cap).
"""

from datetime import UTC, datetime, timedelta
from typing import Final

from src.config import get_logger
from src.domain.entities.shared import JsonDict
from src.domain.repositories.uow import UnitOfWorkProtocol

logger = get_logger(__name__).bind(service="operation_run_reaper")

# Age a still-``running`` row must reach before the startup tick reaps it.
# Hours-scale and generous on purpose:
# - the tick runs as a background startup task beside early traffic, so a run
#   this fresh process just started CAN already exist when the tick fires; the
#   bound is what keeps it (and any future second machine's live runs) safe;
# - the longest observed single run is 54 minutes (the v0.10.2.5 incident
#   import), but the un-batched Last.fm full-history path measured 4-6h and
#   runs as ONE operation_runs row — 12h is 2x that worst case;
# - a queued multi-file export never lengthens any single run: each queue
#   entry is its own row (a queued-not-started entry has no row at all), so a
#   13-file queue needs no wider bound than one file.
# The price of having no heartbeat column: a dead run shows as ``error`` only
# on the first boot that finds it older than this, not the moment it dies.
REAP_AGE_BOUND: Final = timedelta(hours=12)

# Bounds one tick's write volume. MAX_CONCURRENT_OPERATIONS is 3, so reaching
# this would mean the table itself is wrong, not that the batch is too small.
REAP_MAX_BATCH: Final = 100

# Terse form for ``counts["error_message"]`` (the Import History cell); the
# per-row issue carries the explanation and next step, mirroring the
# CANCELLED_* two-string pattern in ``sse_operations``.
PROCESS_DIED_ERROR_MESSAGE: Final = "process died before the run finished"


def _process_died_issue(minutes_since_start: int) -> JsonDict:
    return {
        "message": (
            "process died: run was still marked running at startup, "
            f"{minutes_since_start} minutes after start — the server was "
            "killed without observing the run's end; re-run it "
            "(re-imports are idempotent)"
        )
    }


async def reap_dead_runs(
    uow: UnitOfWorkProtocol,
    *,
    age_bound: timedelta = REAP_AGE_BOUND,
) -> int:
    """Mark over-age ``running`` rows ``error`` with a process-died issue.

    Idempotent: only rows matching the staleness condition are touched, and a
    reaped row no longer matches. Status and issue land in one transaction
    (single commit), preserving ``finalize_run``'s invariant that an ``error``
    row is never durable without its reason attached. Returns the number of
    rows reaped.
    """
    now = datetime.now(UTC)
    cutoff = now - age_bound
    async with uow:
        repo = uow.get_operation_run_repository()
        dead = await repo.list_running_started_before(cutoff, limit=REAP_MAX_BATCH)
        for run in dead:
            minutes = int((now - run.started_at).total_seconds() // 60)
            await repo.update_status(
                run.id,
                user_id=run.user_id,
                status="error",
                ended_at=now,
                counts={"error_message": PROCESS_DIED_ERROR_MESSAGE},
            )
            await repo.append_issues(
                run.id,
                user_id=run.user_id,
                issues=[_process_died_issue(minutes)],
            )
            logger.warning(
                "Reaped dead operation run",
                run_id=str(run.id),
                operation_type=run.operation_type,
                started_at=run.started_at.isoformat(),
                minutes_since_start=minutes,
            )
        await uow.commit()
        return len(dead)


async def count_running_runs(uow: UnitOfWorkProtocol) -> int:
    """How many ``running`` rows are live by the reaper's own definition.

    The DB half of the pre-deploy busy gate — queued-not-started files have no
    row yet and are counted by the in-process queue registry instead. Rows
    older than ``REAP_AGE_BOUND`` are excluded: the reaper already considers
    them dead, but it only runs at startup — and the deploy this gate guards
    is exactly the restart that would reap them. Counting them would let one
    phantom row block every deploy with nothing able to clear it.
    """
    cutoff = datetime.now(UTC) - REAP_AGE_BOUND
    async with uow:
        repo = uow.get_operation_run_repository()
        return await repo.count_running_started_since(cutoff)


__all__ = [
    "PROCESS_DIED_ERROR_MESSAGE",
    "REAP_AGE_BOUND",
    "REAP_MAX_BATCH",
    "count_running_runs",
    "reap_dead_runs",
]
