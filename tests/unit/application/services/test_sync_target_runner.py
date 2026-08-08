"""Unit tests for the shared sync-target runner.

Covers the behaviour that used to live in the scheduler's ``_dispatch_sync`` —
reading both failure signals, and not letting a flaky audit finalize flip a
successful sync into a recorded failure — plus the poll-hook ordering the
adaptive targets depend on.

The ordering assertions are the load-bearing ones: a veto must open no audit row
(or a 30-minute heartbeat writes dozens of no-op runs a day), and the lease must
be released even when the run explodes (or one crash wedges polling until the
TTL expires).

``TestAuditRowContent`` pins the other half: an unattended run's row records what
a manual one's would. The mapping's own truth table is tested once, at the
mapping — ``test_operation_outcome.py``.
"""

import contextlib
from datetime import timedelta
from unittest.mock import AsyncMock, patch
from uuid import uuid7

import pytest

from src.application.services import sync_target_runner
from src.application.services.sync_target_runner import (
    UnschedulableSyncTargetError,
    run_sync_target,
)
from src.application.use_cases._shared.sync_targets import (
    PollClaim,
    SyncTargetSpec,
)
from src.domain.entities.operations import OperationResult

_TARGET = "lastfm:plays"


def _failed_result() -> OperationResult:
    """An OperationResult that reports a handled failure WITHOUT raising."""
    r = OperationResult(operation_name="import")
    r.summary_metrics.add("errors", 1, "Errors", significance=1)
    r.metadata["error"] = "lastfm session expired"
    return r


@contextlib.contextmanager
def _env(spec: SyncTargetSpec, *, finalize: AsyncMock | None = None):
    """Register one target and stub the audit-row writers."""
    run_id = uuid7()
    with (
        patch.dict(sync_target_runner.SYNC_TARGETS, {_TARGET: spec}, clear=False),
        patch.object(
            sync_target_runner, "start_run", AsyncMock(return_value=run_id)
        ) as m_start,
        patch.object(
            sync_target_runner, "finalize_run", finalize or AsyncMock()
        ) as m_finalize,
    ):
        yield run_id, m_start, m_finalize


def _spec(run: AsyncMock, **kwargs: object) -> SyncTargetSpec:
    return SyncTargetSpec(label="Test target", run=run, **kwargs)  # pyright: ignore[reportArgumentType]


class TestFailureSignals:
    async def test_returned_failed_result_is_a_failure(self) -> None:
        # The sync did NOT raise — it returned an OperationResult reporting
        # errors. Missing this signal records a failed sync as a clean fire.
        run = AsyncMock(return_value=_failed_result())
        with _env(_spec(run)) as (_run_id, _m_start, m_finalize):
            outcome = await run_sync_target("u1", _TARGET, initiated_by="schedule")
        assert outcome.status == "failed"
        assert m_finalize.await_args.kwargs["status"] == "error"

    async def test_clean_result_is_a_success(self) -> None:
        run = AsyncMock(return_value=OperationResult(operation_name="import"))
        with _env(_spec(run)) as (_run_id, _m_start, m_finalize):
            outcome = await run_sync_target("u1", _TARGET, initiated_by="schedule")
        assert outcome.status == "completed"
        assert m_finalize.await_args.kwargs["status"] == "complete"

    async def test_raised_exception_is_a_failure_with_a_safe_label(self) -> None:
        run = AsyncMock(side_effect=RuntimeError("token=abc123"))
        with _env(_spec(run)) as (_run_id, _m_start, m_finalize):
            outcome = await run_sync_target("u1", _TARGET, initiated_by="schedule")
        assert outcome.status == "failed"
        # Type name only — the label reaches schedules.last_error and the UI.
        assert outcome.error_label == "RuntimeError"
        assert m_finalize.await_args.kwargs["status"] == "error"

    async def test_flaky_audit_finalize_does_not_flip_a_success(self) -> None:
        run = AsyncMock(return_value=OperationResult(operation_name="import"))
        flaky = AsyncMock(side_effect=ConnectionError("neon cold pause"))
        with _env(_spec(run), finalize=flaky):
            outcome = await run_sync_target("u1", _TARGET, initiated_by="schedule")
        assert outcome.status == "completed"


class TestAuditRowContent:
    """An unattended run's row must say exactly what a manual one's would.

    Nobody was watching when a scheduled or demand-triggered sync ran, so the row
    IS the report. It used to collapse to complete/error with no counts and no
    issues, while the byte-identical run started from the web recorded ``partial``
    with the unresolved tracks named — the same import, two stories.
    """

    async def test_partial_result_records_partial_with_counts_and_item_issues(
        self,
    ) -> None:
        result = OperationResult(operation_name="import")
        result.summary_metrics.add("track_plays", 480, "Track Plays Created")
        result.summary_metrics.add("errors", 20, "Errors", significance=1)
        result.metadata["error"] = "20 of 500 plays failed import"
        result.metadata["resolution_failures"] = [
            {"track": "Aphex Twin - Xtal", "reason": "track_resolution_failed"}
        ]
        run = AsyncMock(return_value=result)

        with _env(_spec(run)) as (_run_id, _m_start, m_finalize):
            outcome = await run_sync_target("u1", _TARGET, initiated_by="schedule")

        kwargs = m_finalize.await_args.kwargs
        assert kwargs["status"] == "partial"
        assert kwargs["counts"] == {"track_plays": 480, "errors": 20}
        assert kwargs["issues"][0] == {"message": "20 of 500 plays failed import"}
        assert kwargs["issues"][1]["track"] == "Aphex Twin - Xtal"
        # The health signal is unchanged by the presentation split: a partial run
        # still fails the schedule, still backs the poller off.
        assert outcome.status == "failed"

    async def test_total_failure_records_error_with_the_headline_reason(self) -> None:
        run = AsyncMock(return_value=_failed_result())
        with _env(_spec(run)) as (_run_id, _m_start, m_finalize):
            await run_sync_target("u1", _TARGET, initiated_by="schedule")

        kwargs = m_finalize.await_args.kwargs
        assert kwargs["status"] == "error"
        assert kwargs["counts"] == {"errors": 1}
        assert kwargs["issues"] == [{"message": "lastfm session expired"}]

    async def test_clean_result_records_counts_and_no_issues(self) -> None:
        result = OperationResult(operation_name="import")
        result.summary_metrics.add("track_plays", 12, "Track Plays Created")
        run = AsyncMock(return_value=result)

        with _env(_spec(run)) as (_run_id, _m_start, m_finalize):
            await run_sync_target("u1", _TARGET, initiated_by="schedule")

        kwargs = m_finalize.await_args.kwargs
        assert kwargs["status"] == "complete"
        assert kwargs["counts"] == {"track_plays": 12}
        assert not kwargs["issues"]

    async def test_raised_exception_records_its_message_as_an_issue(self) -> None:
        # The row is where `safe_failure_message` sends the reader for detail, so
        # it carries the full message the redacted `error_label` withholds.
        run = AsyncMock(side_effect=RuntimeError("connector 503"))
        with _env(_spec(run)) as (_run_id, _m_start, m_finalize):
            outcome = await run_sync_target("u1", _TARGET, initiated_by="schedule")

        assert outcome.error_label == "RuntimeError"
        assert m_finalize.await_args.kwargs["issues"] == [{"message": "connector 503"}]

    async def test_message_less_exception_records_its_type(self) -> None:
        # `raise KeyError()` has an empty str() — a blank issue is worse than none.
        run = AsyncMock(side_effect=KeyError)
        with _env(_spec(run)) as (_run_id, _m_start, m_finalize):
            await run_sync_target("u1", _TARGET, initiated_by="schedule")

        assert m_finalize.await_args.kwargs["issues"] == [{"message": "KeyError"}]


class TestProvenance:
    async def test_provenance_reaches_the_audit_row(self) -> None:
        run = AsyncMock(return_value=OperationResult(operation_name="import"))
        schedule_id = uuid7()
        with _env(_spec(run)) as (_run_id, m_start, _m_finalize):
            await run_sync_target(
                "u1",
                _TARGET,
                initiated_by="demand",
                trigger_detail="web",
                triggered_by_schedule_id=schedule_id,
            )
        kwargs = m_start.await_args.kwargs
        assert kwargs["initiated_by"] == "demand"
        assert kwargs["trigger_detail"] == "web"
        assert kwargs["triggered_by_schedule_id"] == schedule_id
        # Clean break from "scheduled_sync:*" — one operation type whatever fired it.
        assert kwargs["operation_type"] == f"sync:{_TARGET}"

    async def test_unknown_target_raises_before_opening_a_run(self) -> None:
        with patch.object(sync_target_runner, "start_run", AsyncMock()) as m_start:
            with pytest.raises(UnschedulableSyncTargetError):
                await run_sync_target("u1", "x:gone", initiated_by="schedule")
        m_start.assert_not_awaited()


class TestPollHooks:
    async def test_vetoed_poll_opens_no_audit_row_and_does_not_run(self) -> None:
        """The ordering guarantee the whole veto design exists for.

        A 30-minute heartbeat that vetoed *after* start_run would write ~48
        no-op OperationRun rows per user per day, drowning the run log it was
        meant to make legible.
        """
        run = AsyncMock()
        begin = AsyncMock(return_value=PollClaim(granted=False, veto_reason="not_due"))
        finish = AsyncMock()
        spec = _spec(run, try_begin_poll=begin, finish_poll=finish)

        with _env(spec) as (_run_id, m_start, m_finalize):
            outcome = await run_sync_target("u1", _TARGET, initiated_by="schedule")

        assert outcome.status == "vetoed"
        assert outcome.veto_reason == "not_due"
        m_start.assert_not_awaited()
        m_finalize.assert_not_awaited()
        run.assert_not_awaited()
        # Nothing was claimed, so nothing needs releasing.
        finish.assert_not_awaited()

    async def test_granted_poll_runs_and_reports_success_to_finish(self) -> None:
        run = AsyncMock(return_value=OperationResult(operation_name="import"))
        begin = AsyncMock(return_value=PollClaim(granted=True, payload="carried"))
        finish = AsyncMock()
        spec = _spec(run, try_begin_poll=begin, finish_poll=finish)

        with _env(spec):
            await run_sync_target("u1", _TARGET, initiated_by="schedule")

        outcome = finish.await_args.args[0]
        assert outcome.succeeded is True
        # The opaque payload round-trips untouched — try_begin_poll uses it to
        # hand pre-poll state to its own finish hook without a second DB read.
        assert outcome.claim.payload == "carried"

    async def test_finish_still_runs_when_the_sync_raises(self) -> None:
        # Without this, one crash strands the lease until its TTL expires and
        # polling stops for a quarter of an hour.
        run = AsyncMock(side_effect=RuntimeError("boom"))
        begin = AsyncMock(return_value=PollClaim(granted=True))
        finish = AsyncMock()
        spec = _spec(run, try_begin_poll=begin, finish_poll=finish)

        with _env(spec):
            outcome = await run_sync_target("u1", _TARGET, initiated_by="schedule")

        assert outcome.status == "failed"
        finish.assert_awaited_once()
        assert finish.await_args.args[0].succeeded is False

    async def test_soft_failure_is_reported_to_finish_as_unsuccessful(self) -> None:
        # A returned-but-failed result must not look like a productive poll, or
        # the backoff would reset on a sync that fetched nothing.
        run = AsyncMock(return_value=_failed_result())
        begin = AsyncMock(return_value=PollClaim(granted=True))
        finish = AsyncMock()
        spec = _spec(run, try_begin_poll=begin, finish_poll=finish)

        with _env(spec):
            await run_sync_target("u1", _TARGET, initiated_by="schedule")

        assert finish.await_args.args[0].succeeded is False

    async def test_a_broken_finish_hook_does_not_mask_the_run_outcome(self) -> None:
        run = AsyncMock(return_value=OperationResult(operation_name="import"))
        begin = AsyncMock(return_value=PollClaim(granted=True))
        finish = AsyncMock(side_effect=ConnectionError("neon cold pause"))
        spec = _spec(run, try_begin_poll=begin, finish_poll=finish)

        with _env(spec):
            outcome = await run_sync_target("u1", _TARGET, initiated_by="schedule")

        # The sync genuinely succeeded; a bookkeeping error must not rewrite that.
        assert outcome.status == "completed"

    async def test_caller_max_age_reaches_the_begin_hook(self) -> None:
        run = AsyncMock(return_value=OperationResult(operation_name="import"))
        begin = AsyncMock(return_value=PollClaim(granted=True))
        spec = _spec(run, try_begin_poll=begin, finish_poll=AsyncMock())

        with _env(spec):
            await run_sync_target(
                "u1",
                _TARGET,
                initiated_by="demand",
                trigger="demand",
                max_age=timedelta(minutes=3),
            )

        context = begin.await_args.args[0]
        assert context.trigger == "demand"
        assert context.max_age == timedelta(minutes=3)

    async def test_target_without_hooks_always_runs(self) -> None:
        run = AsyncMock(return_value=OperationResult(operation_name="import"))
        with _env(_spec(run)):
            outcome = await run_sync_target("u1", _TARGET, initiated_by="schedule")
        assert outcome.status == "completed"
        run.assert_awaited_once()


class TestCancellation:
    """Shutdown must release the lease without swallowing the cancellation."""

    async def test_cancellation_still_releases_the_poll_lease(self) -> None:
        """Regression: `suppress(Exception)` does not catch `CancelledError`.

        A rolling deploy cancels the task mid-poll. The `finally` runs, but the
        `finish_poll` await re-raised immediately, so `poll_claimed_at` was never
        cleared and the next trigger lost the claim for the full 15-minute TTL.
        """
        import asyncio

        run = AsyncMock(side_effect=asyncio.CancelledError())
        begin = AsyncMock(return_value=PollClaim(granted=True))
        finish = AsyncMock()
        spec = _spec(run, try_begin_poll=begin, finish_poll=finish)

        with _env(spec):
            with pytest.raises(asyncio.CancelledError):
                await run_sync_target("u1", _TARGET, initiated_by="schedule")

        # Released on the way out...
        finish.assert_awaited_once()
        assert finish.await_args.args[0].succeeded is False

    async def test_a_cancelled_release_does_not_mask_the_cancellation(self) -> None:
        # Suppressing CancelledError around finish_poll must not swallow the
        # drain signal itself — the caller has to see it and stop.
        import asyncio

        run = AsyncMock(side_effect=asyncio.CancelledError())
        begin = AsyncMock(return_value=PollClaim(granted=True))
        finish = AsyncMock(side_effect=asyncio.CancelledError())
        spec = _spec(run, try_begin_poll=begin, finish_poll=finish)

        with _env(spec):
            with pytest.raises(asyncio.CancelledError):
                await run_sync_target("u1", _TARGET, initiated_by="schedule")
