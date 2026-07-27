"""Unit tests for the stalled-workflow-run sweeper.

Verifies the sweeper distinguishes cold-start hangs (no heartbeat ever)
from mid-execution stalls (heartbeat went silent), produces the right
``error_message`` for each, is robust to per-row failures, and paces itself by
run activity so an idle deployment lets Neon's compute suspend.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.application.services.run_activity import reset_run_activity, track_run
from src.application.services.workflow_run_sweeper import (
    IDLE_SWEEP_INTERVAL_SECONDS,
    SWEEP_INTERVAL_SECONDS,
    run_sweeper_loop,
    sweep_stalled_runs,
)
from src.config.constants import WorkflowConstants
from src.domain.entities.workflow import WorkflowRun
from tests.fixtures import make_mock_uow, make_mock_workflow_run_repo


def _running_run(*, heartbeat_at: datetime | None, started_at: datetime) -> WorkflowRun:
    return WorkflowRun(
        id=uuid4(),
        workflow_id=uuid4(),
        status=WorkflowConstants.RUN_STATUS_RUNNING,
        started_at=started_at,
        heartbeat_at=heartbeat_at,
    )


def _patch_loop_execute(repo, *, ticks: int):
    """Run the sweeper loop against a mock UoW, cancelling after ``ticks``."""
    uow = make_mock_uow(workflow_run_repo=repo)
    calls = 0

    async def fake_execute(tick, user_id=None):
        nonlocal calls
        calls += 1
        if calls > ticks:
            raise asyncio.CancelledError
        return await tick(uow)

    return patch(
        "src.application.services.periodic_loop.execute_use_case",
        AsyncMock(side_effect=fake_execute),
    )


async def _collect_delays(repo, *, ticks: int) -> list[float]:
    """Sleep durations the loop chose between ``ticks`` sweeps.

    The sweeper always passes a real wake event, so its sleep goes through
    ``wait_for`` rather than ``sleep``. Patching ``wait_for`` records the chosen
    timeout without waiting it out — without this the idle case would block for
    the full six-hour fallback.
    """
    recorded: list[float] = []

    # ``**kwargs`` rather than a literal ``timeout=`` parameter: the loop always
    # passes it by keyword, and naming it here would trip ASYNC109.
    async def fake_wait_for(awaitable, **kwargs: float):
        awaitable.close()
        recorded.append(kwargs["timeout"])

    with _patch_loop_execute(repo, ticks=ticks):
        with patch(
            "src.application.services.periodic_loop.asyncio.wait_for",
            AsyncMock(side_effect=fake_wait_for),
        ):
            with pytest.raises(asyncio.CancelledError):
                await run_sweeper_loop()
    return recorded


async def _run_loop_once(repo) -> int:
    """Drive exactly one sweep, returning how many sweeps actually ran."""
    _ = await _collect_delays(repo, ticks=1)
    return repo.list_stalled_runs.await_count


class TestSweepStalledRuns:
    async def test_no_stalled_runs_no_writes(self) -> None:
        repo = make_mock_workflow_run_repo(list_stalled_runs=[])
        uow = make_mock_uow(workflow_run_repo=repo)

        count = await sweep_stalled_runs(uow, stale_threshold_seconds=60)

        assert count == 0
        repo.update_run_status.assert_not_awaited()

    async def test_cold_start_run_marked_with_diagnostic_message(self) -> None:
        now = datetime.now(UTC)
        cold_start_run = _running_run(
            heartbeat_at=None, started_at=now - timedelta(seconds=120)
        )
        repo = make_mock_workflow_run_repo(list_stalled_runs=[cold_start_run])
        uow = make_mock_uow(workflow_run_repo=repo)

        count = await sweep_stalled_runs(uow, stale_threshold_seconds=60)

        assert count == 1
        repo.update_run_status.assert_awaited_once()
        call = repo.update_run_status.await_args
        assert call.args[0] == cold_start_run.id
        # A stall is an operational event (worker died / loop blocked), recorded
        # CRASHED — not FAILED, which is reserved for workflow logic raising.
        assert call.args[1] == WorkflowConstants.RUN_STATUS_CRASHED
        assert "cold-start hang" in call.kwargs["error_message"]
        assert call.kwargs["completed_at"] is not None
        # duration_ms should be ~120000ms — let it be at least 100s worth
        assert call.kwargs["duration_ms"] is not None
        assert call.kwargs["duration_ms"] >= 100_000

    async def test_stalled_mid_execution_uses_watchdog_message(self) -> None:
        now = datetime.now(UTC)
        stalled = _running_run(
            heartbeat_at=now - timedelta(seconds=120),
            started_at=now - timedelta(seconds=300),
        )
        repo = make_mock_workflow_run_repo(list_stalled_runs=[stalled])
        uow = make_mock_uow(workflow_run_repo=repo)

        count = await sweep_stalled_runs(uow, stale_threshold_seconds=60)

        assert count == 1
        call = repo.update_run_status.await_args
        assert "watchdog" in call.kwargs["error_message"]
        assert "cold-start" not in call.kwargs["error_message"]

    async def test_per_row_failure_does_not_stop_sweep(self) -> None:
        now = datetime.now(UTC)
        bad = _running_run(heartbeat_at=None, started_at=now - timedelta(seconds=120))
        good = _running_run(heartbeat_at=None, started_at=now - timedelta(seconds=120))

        repo = make_mock_workflow_run_repo(list_stalled_runs=[bad, good])
        # First update raises; second transitions a row (returns True).
        repo.update_run_status.side_effect = [RuntimeError("DB blip"), True]
        uow = make_mock_uow(workflow_run_repo=repo)

        count = await sweep_stalled_runs(uow, stale_threshold_seconds=60)

        # Only one row succeeded, but the loop kept going.
        assert count == 1
        assert repo.update_run_status.await_count == 2

    async def test_already_terminal_run_not_counted(self) -> None:
        """A run that won the race to terminal between list_stalled_runs and the
        write makes the guarded UPDATE no-op (returns False). It must not inflate
        the crash count — the sweeper only counts rows it actually transitioned.
        """
        now = datetime.now(UTC)
        raced = _running_run(heartbeat_at=None, started_at=now - timedelta(seconds=120))
        transitioned = _running_run(
            heartbeat_at=None, started_at=now - timedelta(seconds=120)
        )
        repo = make_mock_workflow_run_repo(list_stalled_runs=[raced, transitioned])
        # First write lost the race (no-op → False); second genuinely transitioned.
        repo.update_run_status.side_effect = [False, True]
        uow = make_mock_uow(workflow_run_repo=repo)

        count = await sweep_stalled_runs(uow, stale_threshold_seconds=60)

        # Both writes were attempted, but only the real transition is counted.
        assert repo.update_run_status.await_count == 2
        assert count == 1

    async def test_threshold_and_batch_cap_passed_through_to_repo(self) -> None:
        repo = make_mock_workflow_run_repo(list_stalled_runs=[])
        uow = make_mock_uow(workflow_run_repo=repo)

        await sweep_stalled_runs(uow, stale_threshold_seconds=42)

        # The sweep is batch-capped per cycle so a large backlog can't make one
        # tick unbounded.
        repo.list_stalled_runs.assert_awaited_once_with(
            stale_threshold_seconds=42,
            limit=WorkflowConstants.SWEEP_MAX_BATCH,
        )


class TestSweeperCadence:
    """The loop's pacing is what keeps Neon's compute suspendable when idle.

    A stalled run can only appear while a run is executing, or after a process
    death (which the startup tick covers), so an idle sweeper has nothing to look
    for. Sweeping anyway on a 30s rhythm reset Neon's 5-minute scale-to-zero
    timer forever.
    """

    @pytest.fixture(autouse=True)
    def _fresh_signal(self):
        reset_run_activity()
        yield
        reset_run_activity()

    async def test_sweeps_immediately_on_startup(self) -> None:
        # Recovers runs orphaned by a prior process kill — the one case the
        # in-process activity counter can never see.
        repo = make_mock_workflow_run_repo(list_stalled_runs=[])
        swept = await _run_loop_once(repo)
        assert swept == 1

    async def test_idle_loop_waits_the_long_fallback(self) -> None:
        repo = make_mock_workflow_run_repo(list_stalled_runs=[])
        delays = await _collect_delays(repo, ticks=2)
        assert delays == [IDLE_SWEEP_INTERVAL_SECONDS] * 2

    async def test_active_loop_holds_the_short_cadence(self) -> None:
        repo = make_mock_workflow_run_repo(list_stalled_runs=[])
        async with track_run():
            delays = await _collect_delays(repo, ticks=2)
        assert delays == [SWEEP_INTERVAL_SECONDS] * 2

    async def test_idle_fallback_is_longer_than_neon_suspend_window(self) -> None:
        # A query every T keeps a 5-minute-timeout compute awake min(5min, T) of
        # every T. The fallback has to sit far above that window or the sweeper
        # alone would keep billing.
        assert IDLE_SWEEP_INTERVAL_SECONDS > 30 * 60
