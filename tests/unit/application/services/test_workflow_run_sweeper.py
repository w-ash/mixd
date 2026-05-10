"""Unit tests for the stalled-workflow-run sweeper.

Verifies the sweeper distinguishes cold-start hangs (no heartbeat ever)
from mid-execution stalls (heartbeat went silent), produces the right
``error_message`` for each, and is robust to per-row failures.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from src.application.services.workflow_run_sweeper import (
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
        assert call.args[1] == WorkflowConstants.RUN_STATUS_FAILED
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
        # First update fails; second succeeds.
        repo.update_run_status.side_effect = [RuntimeError("DB blip"), None]
        uow = make_mock_uow(workflow_run_repo=repo)

        count = await sweep_stalled_runs(uow, stale_threshold_seconds=60)

        # Only one row succeeded, but the loop kept going.
        assert count == 1
        assert repo.update_run_status.await_count == 2

    async def test_threshold_passed_through_to_repo(self) -> None:
        repo = make_mock_workflow_run_repo(list_stalled_runs=[])
        uow = make_mock_uow(workflow_run_repo=repo)

        await sweep_stalled_runs(uow, stale_threshold_seconds=42)

        repo.list_stalled_runs.assert_awaited_once_with(stale_threshold_seconds=42)
