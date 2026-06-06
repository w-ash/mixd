"""Unit tests for the workflow/sync scheduler orchestration.

The dispatch helpers and schedule-state writes are thin ``execute_use_case``
wrappers (covered by the repo integration suite); these tests patch them out and
assert the decision logic the scheduler owns:

- ``_classify_run_status`` — status → disposition mapping.
- ``_dispatch_sync`` — reads BOTH failure signals (a raised exception AND a
  returned ``OperationResult`` that reports errors), and never lets a flaky audit
  finalize flip a successful sync into a failure (the v0.8.2 review fixes).
- ``_process_one`` — claim-or-skip, disposition routing, the already-running
  streak reset, the unschedulable-target auto-disable, the leak-safe label.
- ``run_scheduler_tick`` — the reaper-as-skip and the per-dispatch timeout.
"""

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch
from uuid import uuid7

import pytest

from src.application.services import scheduler
from src.application.services.scheduler import (
    _classify_run_status,
    _dispatch_sync,
    _DispatchOutcome,
    _process_one,
    _safe_failure_message,
    _UnschedulableTargetError,
    run_scheduler_tick,
)
from src.domain.entities.operations import OperationResult
from src.domain.entities.schedule import Schedule
from src.domain.exceptions import WorkflowAlreadyRunningError
from tests.fixtures import make_mock_uow

pytestmark = pytest.mark.unit


def _wf_schedule(*, next_run_at: datetime) -> Schedule:
    return Schedule(user_id="u1", workflow_id=uuid7(), hour=6, next_run_at=next_run_at)


def _sync_schedule(*, next_run_at: datetime, target: str = "lastfm:plays") -> Schedule:
    return Schedule(user_id="u1", sync_target=target, hour=6, next_run_at=next_run_at)


def _failed_result() -> OperationResult:
    """An OperationResult that reports a handled failure WITHOUT raising."""
    r = OperationResult(operation_name="import")
    r.summary_metrics.add("errors", 1, "Errors", significance=1)
    r.metadata["error"] = "lastfm session expired"
    return r


@contextlib.contextmanager
def _patched(**overrides):
    """Patch the dispatch + schedule-write helpers; yield the mock namespace."""
    names = {
        "_claim": AsyncMock(return_value=True),
        "_dispatch_workflow": AsyncMock(),
        "_dispatch_sync": AsyncMock(),
        "_release": AsyncMock(),
        "_disable": AsyncMock(),
        # Stub the fresh re-read so _process_one doesn't open a real UoW.
        "_advance_from_fresh": AsyncMock(return_value=datetime.now(UTC)),
    }
    names.update(overrides)
    with patch.multiple("src.application.services.scheduler", **names):
        yield names


async def _run_process(
    schedule: Schedule, *, catchup: bool = True, now: datetime | None = None
) -> None:
    await _process_one(
        schedule,
        now=now or datetime.now(UTC),
        update_run_status=AsyncMock(),
        update_node_status=AsyncMock(),
        catchup=catchup,
        grace_seconds=120,
    )


class TestClassifyRunStatus:
    def test_failed_and_crashed_are_failure(self) -> None:
        assert _classify_run_status("failed") == "failure"
        assert _classify_run_status("crashed") == "failure"

    def test_cancelled_is_skip(self) -> None:
        assert _classify_run_status("cancelled") == "skip"

    def test_completed_is_success(self) -> None:
        assert _classify_run_status("completed") == "success"


class TestProcessOne:
    async def test_claim_lost_does_nothing(self) -> None:
        with _patched(_claim=AsyncMock(return_value=False)) as m:
            await _run_process(_wf_schedule(next_run_at=datetime.now(UTC)))
        m["_dispatch_workflow"].assert_not_awaited()
        m["_release"].assert_not_awaited()
        m["_disable"].assert_not_awaited()

    async def test_workflow_success_marks_completed(self) -> None:
        run_id = uuid7()
        with _patched(
            _dispatch_workflow=AsyncMock(
                return_value=_DispatchOutcome(
                    run_id=run_id, disposition="success", status="completed"
                )
            )
        ) as m:
            await _run_process(_wf_schedule(next_run_at=datetime.now(UTC)))
        m["_release"].assert_awaited_once()
        kwargs = m["_release"].await_args.kwargs
        assert kwargs["disposition"] == "success"
        assert kwargs["last_run_id"] == run_id

    async def test_workflow_failure_status_marks_failed(self) -> None:
        with _patched(
            _dispatch_workflow=AsyncMock(
                return_value=_DispatchOutcome(
                    run_id=uuid7(),
                    disposition="failure",
                    status="failed",
                    error_label="run ended: failed",
                )
            )
        ) as m:
            await _run_process(_wf_schedule(next_run_at=datetime.now(UTC)))
        kwargs = m["_release"].await_args.kwargs
        assert kwargs["disposition"] == "failure"
        assert kwargs["last_error"] == "run ended: failed"

    async def test_cancelled_run_skips_not_completed(self) -> None:
        # A drained run (disposition='skip') must not bump run_count / reset the
        # failure streak — it routes through _release as a plain skip.
        with _patched(
            _dispatch_workflow=AsyncMock(
                return_value=_DispatchOutcome(
                    run_id=uuid7(), disposition="skip", status="cancelled"
                )
            )
        ) as m:
            await _run_process(_wf_schedule(next_run_at=datetime.now(UTC)))
        kwargs = m["_release"].await_args.kwargs
        assert kwargs["disposition"] == "skip"
        assert kwargs["last_run_status"] == "cancelled"
        # A plain (non-already-running) skip does NOT reset the streak.
        assert kwargs.get("reset_failures", False) is False

    async def test_already_running_skips_and_resets_streak(self) -> None:
        with _patched(
            _dispatch_workflow=AsyncMock(side_effect=WorkflowAlreadyRunningError("wf"))
        ) as m:
            await _run_process(_wf_schedule(next_run_at=datetime.now(UTC)))
        kwargs = m["_release"].await_args.kwargs
        assert kwargs["disposition"] == "skip"
        assert kwargs["last_run_status"] == "skipped_already_running"
        # The workflow is demonstrably healthy → clear any stale failure banner.
        assert kwargs["reset_failures"] is True

    async def test_unschedulable_target_disables_schedule(self) -> None:
        with _patched(
            _dispatch_sync=AsyncMock(
                side_effect=_UnschedulableTargetError("lastfm:gone")
            )
        ) as m:
            await _run_process(_sync_schedule(next_run_at=datetime.now(UTC)))
        m["_disable"].assert_awaited_once()
        # An orphaned target is disabled, NOT recorded as a per-tick failure.
        m["_release"].assert_not_awaited()

    async def test_dispatch_exception_marks_failed_leak_safe(self) -> None:
        class TokenLeakingError(Exception):
            pass

        with _patched(
            _dispatch_sync=AsyncMock(
                side_effect=TokenLeakingError("Bearer sk-secret-xyz")
            )
        ) as m:
            await _run_process(_sync_schedule(next_run_at=datetime.now(UTC)))
        kwargs = m["_release"].await_args.kwargs
        assert kwargs["disposition"] == "failure"
        # The raw message (with the token) must never reach last_error.
        assert kwargs["last_error"] == "TokenLeakingError"
        assert "secret" not in kwargs["last_error"]

    async def test_missed_window_skips_without_dispatch(self) -> None:
        stale = datetime.now(UTC) - timedelta(hours=3)
        with _patched() as m:
            await _run_process(_wf_schedule(next_run_at=stale), catchup=False)
        m["_dispatch_workflow"].assert_not_awaited()
        kwargs = m["_release"].await_args.kwargs
        assert kwargs["disposition"] == "skip"
        assert kwargs["last_run_status"] == "skipped_missed"

    async def test_missed_window_fires_when_catchup_enabled(self) -> None:
        stale = datetime.now(UTC) - timedelta(hours=3)
        with _patched(
            _dispatch_workflow=AsyncMock(
                return_value=_DispatchOutcome(
                    run_id=uuid7(), disposition="success", status="completed"
                )
            )
        ) as m:
            await _run_process(_wf_schedule(next_run_at=stale), catchup=True)
        m["_dispatch_workflow"].assert_awaited_once()
        assert m["_release"].await_args.kwargs["disposition"] == "success"

    async def test_sync_success_marks_completed(self) -> None:
        op_id = uuid7()
        with _patched(
            _dispatch_sync=AsyncMock(
                return_value=_DispatchOutcome(
                    run_id=op_id, disposition="success", status="completed"
                )
            )
        ) as m:
            await _run_process(_sync_schedule(next_run_at=datetime.now(UTC)))
        kwargs = m["_release"].await_args.kwargs
        assert kwargs["disposition"] == "success"
        assert kwargs["last_run_id"] == op_id


class TestDispatchSync:
    """The v0.8.2 review fixes: read the returned OperationResult, and don't let a
    flaky audit finalize flip a successful sync into a recorded failure."""

    @contextlib.contextmanager
    def _sync_env(self, run_sync: AsyncMock, *, finalize: AsyncMock | None = None):
        op_run_id = uuid7()
        with (
            patch.dict(
                scheduler.SYNC_DISPATCH, {"lastfm:plays": run_sync}, clear=False
            ),
            patch.object(scheduler, "start_run", AsyncMock(return_value=op_run_id)),
            patch.object(
                scheduler, "finalize_run", finalize or AsyncMock()
            ) as m_finalize,
        ):
            yield op_run_id, m_finalize

    async def test_returned_failed_result_is_failure(self) -> None:
        # The sync did NOT raise — it returned an OperationResult reporting errors.
        run_sync = AsyncMock(return_value=_failed_result())
        with self._sync_env(run_sync) as (_op_id, m_finalize):
            outcome = await _dispatch_sync(
                _sync_schedule(next_run_at=datetime.now(UTC))
            )
        assert outcome.disposition == "failure"
        # Audit row finalized as error to match the real outcome.
        assert m_finalize.await_args.kwargs["status"] == "error"

    async def test_clean_result_is_success(self) -> None:
        run_sync = AsyncMock(return_value=OperationResult(operation_name="import"))
        with self._sync_env(run_sync) as (_op_id, m_finalize):
            outcome = await _dispatch_sync(
                _sync_schedule(next_run_at=datetime.now(UTC))
            )
        assert outcome.disposition == "success"
        assert m_finalize.await_args.kwargs["status"] == "complete"

    async def test_raised_exception_is_failure(self) -> None:
        run_sync = AsyncMock(side_effect=RuntimeError("boom"))
        with self._sync_env(run_sync) as (_op_id, m_finalize):
            outcome = await _dispatch_sync(
                _sync_schedule(next_run_at=datetime.now(UTC))
            )
        assert outcome.disposition == "failure"
        assert outcome.error_label == "RuntimeError"
        assert m_finalize.await_args.kwargs["status"] == "error"

    async def test_audit_finalize_failure_does_not_flip_success(self) -> None:
        # The sync SUCCEEDED but the audit-row finalize raises (transient DB).
        # The schedule outcome must still be success — not a false failure.
        run_sync = AsyncMock(return_value=OperationResult(operation_name="import"))
        flaky_finalize = AsyncMock(side_effect=ConnectionError("neon cold pause"))
        with self._sync_env(run_sync, finalize=flaky_finalize):
            outcome = await _dispatch_sync(
                _sync_schedule(next_run_at=datetime.now(UTC))
            )
        assert outcome.disposition == "success"

    async def test_unknown_target_raises_unschedulable(self) -> None:
        with patch.object(scheduler, "start_run", AsyncMock()) as m_start:
            with pytest.raises(_UnschedulableTargetError):
                await _dispatch_sync(
                    _sync_schedule(next_run_at=datetime.now(UTC), target="x:gone")
                )
        # Resolved BEFORE opening the audit row → no dangling OperationRun.
        m_start.assert_not_awaited()


class TestSafeFailureMessage:
    def test_returns_class_name_only(self) -> None:
        class SpotifyAuthError(Exception):
            pass

        msg = _safe_failure_message(SpotifyAuthError("token=abc123 leaked"))
        assert msg == "SpotifyAuthError"


class TestSchedulerTick:
    async def test_reaps_stuck_as_skip_then_dispatches_due(self) -> None:
        now = datetime.now(UTC)
        stuck = _sync_schedule(next_run_at=now - timedelta(hours=1))
        due1 = _wf_schedule(next_run_at=now)
        due2 = _sync_schedule(next_run_at=now)

        repo = AsyncMock()
        repo.try_acquire_poll_lock.return_value = True
        repo.list_stuck_started.return_value = [stuck]
        repo.find_due_schedules.return_value = [due1, due2]
        uow = make_mock_uow(schedule_repo=repo)

        with patch.object(scheduler, "_process_one", AsyncMock()) as m_process:
            count = await run_scheduler_tick(
                uow,
                now=now,
                update_run_status=AsyncMock(),
                update_node_status=AsyncMock(),
                max_concurrent=2,
                stuck_timeout_seconds=1800,
                dispatch_timeout_seconds=900,
                catchup=False,
                grace_seconds=120,
            )

        # The reaper now records a SKIP (no failure-streak bump), not a failure.
        repo.mark_schedule_skipped.assert_awaited_once()
        assert (
            repo.mark_schedule_skipped.await_args.kwargs["last_run_status"] == "reaped"
        )
        repo.mark_schedule_failed.assert_not_awaited()
        assert m_process.await_count == 2
        assert count == 2

    async def test_no_due_returns_zero(self) -> None:
        repo = AsyncMock()
        repo.try_acquire_poll_lock.return_value = True
        repo.list_stuck_started.return_value = []
        repo.find_due_schedules.return_value = []
        uow = make_mock_uow(schedule_repo=repo)

        count = await run_scheduler_tick(
            uow,
            now=datetime.now(UTC),
            update_run_status=AsyncMock(),
            update_node_status=AsyncMock(),
            max_concurrent=2,
            stuck_timeout_seconds=1800,
            dispatch_timeout_seconds=900,
            catchup=False,
            grace_seconds=120,
        )
        assert count == 0

    async def test_skips_scan_when_poll_lock_unavailable(self) -> None:
        # Another replica holds this tick's poll lock → skip the scan entirely
        # (no redundant cross-tenant queries), returning 0.
        repo = AsyncMock()
        repo.try_acquire_poll_lock.return_value = False
        uow = make_mock_uow(schedule_repo=repo)

        count = await run_scheduler_tick(
            uow,
            now=datetime.now(UTC),
            update_run_status=AsyncMock(),
            update_node_status=AsyncMock(),
            max_concurrent=2,
            stuck_timeout_seconds=1800,
            dispatch_timeout_seconds=900,
            catchup=False,
            grace_seconds=120,
        )
        assert count == 0
        repo.list_stuck_started.assert_not_awaited()
        repo.find_due_schedules.assert_not_awaited()

    async def test_hung_dispatch_times_out_as_failure_isolating_siblings(self) -> None:
        now = datetime.now(UTC)
        slow = _wf_schedule(next_run_at=now)
        fast = _wf_schedule(next_run_at=now)

        repo = AsyncMock()
        repo.try_acquire_poll_lock.return_value = True
        repo.list_stuck_started.return_value = []
        repo.find_due_schedules.return_value = [slow, fast]
        uow = make_mock_uow(schedule_repo=repo)

        async def _process(schedule: Schedule, **_kw: object) -> None:
            if schedule.id == slow.id:
                await asyncio.sleep(5)  # never completes within the timeout

        with (
            patch.object(scheduler, "_process_one", AsyncMock(side_effect=_process)),
            patch.object(scheduler, "_release", AsyncMock()) as m_release,
            patch.object(scheduler, "_advance_from_fresh", AsyncMock(return_value=now)),
        ):
            count = await run_scheduler_tick(
                uow,
                now=now,
                update_run_status=AsyncMock(),
                update_node_status=AsyncMock(),
                max_concurrent=2,
                stuck_timeout_seconds=1800,
                dispatch_timeout_seconds=0.01,
                catchup=False,
                grace_seconds=120,
            )

        # The fast dispatch was NOT cancelled by its hung sibling (tick returns 2).
        assert count == 2
        # The hung dispatch was recorded as a timeout failure.
        timeouts = [
            c
            for c in m_release.await_args_list
            if c.kwargs.get("last_run_status") == "timeout"
        ]
        assert len(timeouts) == 1
        assert timeouts[0].kwargs["disposition"] == "failure"
