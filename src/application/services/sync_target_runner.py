"""One execution path for every sync target, whatever triggered it.

The scheduler heartbeat, a play-surface read, an agent tool call, and a workflow
about to filter on play history all run a sync target through this function. That
matters beyond tidiness: the ``OperationRun`` audit row, the checkpoint update,
and the run-log entry are all produced *here*, so a demand-triggered poll cannot
quietly become a second, invisible class of background work. Extracted from the
scheduler's ``_dispatch_sync``, which now delegates to it.

**Order is the specification.** The poll veto runs *before* ``start_run``, so a
heartbeat that decides not to poll leaves no audit row at all — at a 30-minute
cadence, vetoing after opening the row would write dozens of no-op runs per user
per day and drown the surface it was meant to make legible. The release of the
poll lease sits in a ``finally``, so a crash frees it immediately rather than
leaving the next trigger to wait out the TTL.
"""

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from attrs import define

from src.application.services.operation_outcome import audit_outcome, failure_issues
from src.application.services.operation_run_recorder import finalize_run, start_run
from src.application.use_cases._shared.sync_targets import (
    SYNC_TARGETS,
    PollClaim,
    PollOutcome,
    PollTriggerKind,
    TriggerContext,
    sync_result_failed,
)
from src.config import get_logger
from src.domain.entities.operation_run import FAILED_STATUSES
from src.domain.entities.operations import OperationResult

logger = get_logger(__name__)

type SyncRunStatus = Literal["completed", "failed", "vetoed"]


class UnschedulableSyncTargetError(Exception):
    """A schedule names a sync target no longer present in ``SYNC_TARGETS``.

    Handled by the scheduler as an auto-disable (a maintenance event — a
    connector removed while a schedule for it still exists), NOT as a per-tick
    failure that would re-fire and re-fail forever.
    """

    def __init__(self, target: str) -> None:
        self.target = target
        super().__init__(f"unschedulable sync target {target!r}")


@define(frozen=True, slots=True)
class SyncTargetRunOutcome:
    """What one run produced, for the caller's own bookkeeping."""

    status: SyncRunStatus
    run_id: UUID | None = None
    # Leak-safe summary suitable for schedules.last_error; set only on failure.
    error_label: str | None = None
    # Set when the target's own policy declined to run — the caller decides
    # whether that is a skip, a no-op, or something to report.
    veto_reason: str | None = None


def safe_failure_message(exc: Exception) -> str:
    """A leak-safe failure summary for ``schedules.last_error``.

    Deliberately the exception CLASS name, never ``str(exc)``: a connector error
    can embed an OAuth token or a signed URL in its message, and this value is
    surfaced in the UI failure banner. The class name (e.g. ``HTTPStatusError``,
    ``SpotifyAuthError``) is enough to triage; full detail lives in the per-run
    audit row that ``triggered_by_schedule_id`` links back to.

    Public and shared with the scheduler, which needs the same guarantee for the
    errors that reach it outside a dispatch. A second copy would let the rule
    fork — one caller widening the redaction while the other keeps persisting a
    raw connector message the UI renders.
    """
    return type(exc).__name__


async def run_sync_target(
    user_id: str,
    target: str,
    *,
    initiated_by: str,
    trigger: PollTriggerKind = "schedule",
    trigger_detail: str | None = None,
    triggered_by_schedule_id: UUID | None = None,
    max_age: timedelta | None = None,
) -> SyncTargetRunOutcome:
    """Run one sync target end to end, recording it exactly once.

    Reads two distinct failure signals, because the sync use cases report a
    handled failure by *returning* an ``OperationResult`` rather than raising —
    without the second check, a failed sync would be recorded as a clean fire.

    The audit row is written through the same
    :mod:`~src.application.services.operation_outcome` mapping the SSE seam uses,
    so a nightly import that resolved 480 of 500 tracks records as ``partial``
    with real counts and the failed tracks named — identical to what the user
    would have seen had they clicked Import themselves. Unattended runs are the
    whole point of the run log: nobody was watching when it happened.

    ``CancelledError`` is a ``BaseException`` and deliberately not caught around
    the run itself: a shutdown mid-sync propagates to the caller, which releases
    its own claim as a drain rather than a fault. It *is* suppressed around the
    lease release in the ``finally``, because otherwise the cancellation would
    re-raise at that await and strand the claim for its whole TTL — see there.
    """
    spec = SYNC_TARGETS.get(target)
    if spec is None:
        raise UnschedulableSyncTargetError(target)

    context = TriggerContext(
        user_id=user_id,
        trigger=trigger,
        now=datetime.now(UTC),
        max_age=max_age,
    )

    claim = PollClaim(granted=True)
    if spec.try_begin_poll is not None:
        claim = await spec.try_begin_poll(context)
        if not claim.granted:
            # Debug, not warning: declining to poll is the expected outcome of
            # most heartbeats, and the conditions worth a user's attention (a
            # missing scope) surface on the connector card instead.
            logger.debug(
                "Sync target declined to run",
                target=target,
                trigger=trigger,
                reason=claim.veto_reason,
            )
            return SyncTargetRunOutcome(status="vetoed", veto_reason=claim.veto_reason)

    run_id = await start_run(
        user_id=user_id,
        operation_type=f"sync:{target}",
        triggered_by_schedule_id=triggered_by_schedule_id,
        initiated_by=initiated_by,
        trigger_detail=trigger_detail,
    )

    succeeded = False
    result: object = None
    try:
        try:
            result = await spec.run(user_id)
        except Exception as exc:
            # The audit row gets the full message where `error_label` gets only
            # the class name: `schedules.last_error` is a persistent banner on the
            # connector card, whereas the run row is the per-run detail view
            # `safe_failure_message` points at for triage. Without this an
            # unattended crash finalizes as a bare "error" with no reason —
            # exactly the "No issues recorded" gap the run log exists to close.
            # str(exc) is empty for argument-less exceptions, where the type name
            # is the only reason left and a blank issue is worse than none.
            reason = str(exc)[:500] or type(exc).__name__
            # finalize is best-effort: a transient audit-write error must not
            # turn a known-failed sync into an unknown one.
            with contextlib.suppress(Exception):
                await finalize_run(
                    run_id,
                    user_id=user_id,
                    status="error",
                    issues=[{"message": reason}],
                )
            return SyncTargetRunOutcome(
                status="failed",
                run_id=run_id,
                error_label=safe_failure_message(exc),
            )

        # Two questions, deliberately answered separately from the same result.
        # `sync_result_failed` is the HEALTH signal: it drives the poll backoff
        # and `schedules.last_error`, and treats a partial run as failed because
        # something did go wrong. `audit_outcome` is the run log's presentation
        # split — `partial` when work still landed — and is shared verbatim with
        # the SSE seam so a scheduled run and the manual run of the same import
        # can never be recorded differently.
        failed = sync_result_failed(result)
        succeeded = not failed
        status, counts = audit_outcome(result)
        issues = (
            failure_issues(result)
            if status in FAILED_STATUSES and isinstance(result, OperationResult)
            else None
        )
        with contextlib.suppress(Exception):
            # One call, so status + counts + issues land in a single transaction:
            # an unattended run that finalized its status and then crashed before
            # its issues is the "errors: N with no message" symptom made durable.
            await finalize_run(
                run_id,
                user_id=user_id,
                status=status,
                counts=counts,
                issues=issues,
            )
        return SyncTargetRunOutcome(
            status="failed" if failed else "completed",
            run_id=run_id,
            error_label="sync reported errors" if failed else None,
        )
    finally:
        if spec.finish_poll is not None:
            # Suppressed: the sync itself already succeeded or failed on its own
            # terms, and a bookkeeping error here must not mask that. The lease's
            # TTL is the backstop if this write is the thing that failed.
            #
            # CancelledError is listed explicitly because it is a BaseException
            # and would otherwise escape: a deploy cancelling this task mid-poll
            # would re-raise at *this* await, skip the release, and strand the
            # lease for the full TTL. Suppressing it here only lets the release
            # finish — the cancellation still propagates from the enclosing
            # frame, so the caller drains normally.
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await spec.finish_poll(
                    PollOutcome(
                        context=context,
                        claim=claim,
                        succeeded=succeeded,
                        result=result,
                    )
                )
