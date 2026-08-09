"""Unit tests for the startup operation-run reaper (v0.10.2.8).

The reaper closes the gap v0.10.2.5's shutdown hook cannot see: a SIGKILL,
OOM kill, or lost machine leaves rows durably ``running`` with no process
left to finalize them. These tests pin the transaction shape (status + issue
under one commit), the age-bound cutoff handed to the repository, and the
busy-gate count that reuses the same query with ``cutoff = now``.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

from src.application.services.operation_run_reaper import (
    PROCESS_DIED_ERROR_MESSAGE,
    REAP_AGE_BOUND,
    REAP_MAX_BATCH,
    count_running_runs,
    reap_dead_runs,
)
from tests.fixtures import make_mock_uow, make_operation_run


def _uow_with_running(runs: list) -> tuple[object, AsyncMock]:
    uow = make_mock_uow()
    repo = AsyncMock()
    repo.list_running_started_before.return_value = runs
    uow.get_operation_run_repository = lambda: repo
    return uow, repo


class TestReapDeadRuns:
    async def test_stale_row_reaped_as_error_with_process_died_issue(self) -> None:
        stale = make_operation_run(
            user_id="alice",
            started_at=datetime.now(UTC) - timedelta(hours=13),
        )
        uow, repo = _uow_with_running([stale])

        reaped = await reap_dead_runs(uow)

        assert reaped == 1
        update_kwargs = repo.update_status.await_args.kwargs
        assert repo.update_status.await_args.args == (stale.id,)
        assert update_kwargs["user_id"] == "alice"
        assert update_kwargs["status"] == "error"
        assert update_kwargs["ended_at"] is not None
        assert update_kwargs["counts"] == {"error_message": PROCESS_DIED_ERROR_MESSAGE}
        issues = repo.append_issues.await_args.kwargs["issues"]
        assert len(issues) == 1
        assert "process died" in issues[0]["message"]
        assert "minutes after start" in issues[0]["message"]

    async def test_status_and_issue_share_one_commit(self) -> None:
        # A crash between the status write and the issue append would durably
        # record an ``error`` run with no reason — the exact symptom
        # ``finalize_run`` exists to prevent; the reaper keeps its invariant.
        stale = make_operation_run(started_at=datetime.now(UTC) - timedelta(hours=13))
        uow, _repo = _uow_with_running([stale])

        await reap_dead_runs(uow)

        uow.commit.assert_awaited_once()

    async def test_cutoff_is_now_minus_age_bound(self) -> None:
        uow, repo = _uow_with_running([])

        before = datetime.now(UTC)
        await reap_dead_runs(uow)
        after = datetime.now(UTC)

        cutoff = repo.list_running_started_before.await_args.args[0]
        assert before - REAP_AGE_BOUND <= cutoff <= after - REAP_AGE_BOUND
        assert (
            repo.list_running_started_before.await_args.kwargs["limit"]
            == REAP_MAX_BATCH
        )

    async def test_no_stale_rows_writes_nothing(self) -> None:
        uow, repo = _uow_with_running([])

        reaped = await reap_dead_runs(uow)

        assert reaped == 0
        repo.update_status.assert_not_awaited()
        repo.append_issues.assert_not_awaited()


def _uow_with_counts(live: int, stale: int = 0) -> tuple[object, AsyncMock]:
    uow = make_mock_uow()
    repo = AsyncMock()
    repo.count_running_started_since.return_value = live
    repo.count_running_started_before.return_value = stale
    uow.get_operation_run_repository = lambda: repo
    return uow, repo


class TestCountRunningRuns:
    async def test_returns_both_repo_counts_unmodified(self) -> None:
        # One SQL count per window, no Python-side filtering: a
        # fetch-then-filter through the reaper's capped list query silently
        # dropped live runs once 100+ stale rows queued ahead of them, and
        # the gate read "idle".
        uow, _repo = _uow_with_counts(live=2, stale=3)

        counts = await count_running_runs(uow)

        assert counts.live == 2
        assert counts.stale == 3

    async def test_cutoff_reaches_back_the_full_age_bound(self) -> None:
        # A just-started run must count, so the cutoff sits REAP_AGE_BOUND in
        # the past — everything from there to now satisfies started_at >= cutoff.
        uow, repo = _uow_with_counts(0)

        before = datetime.now(UTC)
        await count_running_runs(uow)
        after = datetime.now(UTC)

        cutoff = repo.count_running_started_since.await_args.args[0]
        assert before - REAP_AGE_BOUND <= cutoff <= after - REAP_AGE_BOUND

    async def test_reaper_dead_phantom_does_not_block_the_gate(self) -> None:
        # A row past REAP_AGE_BOUND is dead by the reaper's own definition,
        # but the reaper only runs at startup — and the deploy this gate
        # guards is exactly the restart that would reap it. The exclusion now
        # lives in the SQL predicate: the cutoff handed to the count is never
        # older than the age bound, so a phantom started before it can never
        # be counted (row-level exclusion is pinned in the repository's
        # integration tests).
        uow, repo = _uow_with_counts(0)

        before = datetime.now(UTC)
        await count_running_runs(uow)

        cutoff = repo.count_running_started_since.await_args.args[0]
        assert cutoff >= before - REAP_AGE_BOUND

    async def test_stale_window_complements_the_live_window(self) -> None:
        # The stale count reuses the SAME cutoff with the complementary
        # predicate (started_at < cutoff) — the two windows partition every
        # running row, so live + stale can never double-count or drop one.
        uow, repo = _uow_with_counts(0)

        await count_running_runs(uow)

        live_cutoff = repo.count_running_started_since.await_args.args[0]
        stale_cutoff = repo.count_running_started_before.await_args.args[0]
        assert stale_cutoff == live_cutoff
