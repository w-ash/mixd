"""Unit tests for the shared run-lifecycle helpers.

These helpers are shared by the CLI and API (``src/application/services/
run_lifecycle.py``) so the run lifecycle lives in one place. They are thin
UoW adapters; the *ticker* that calls ``bump_heartbeat`` on a cadence
belongs to ``ExecuteWorkflowRunUseCase`` and is tested alongside it in
``tests/unit/application/use_cases/test_workflow_runs.py``.
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

import src.application.services.run_lifecycle as rl
from tests.fixtures import make_mock_uow


def _fake_runner(uow: MagicMock):
    """Stand-in for ``execute_use_case`` that really invokes the factory.

    Patching the runner rather than the session keeps these unit tests off the
    database while still exercising each helper's lambda, so a wrong repository
    method or a dropped kwarg fails the test.
    """

    async def _run(use_case_factory, user_id=None, *, rollback=True):
        _run.calls.append({"user_id": user_id, "rollback": rollback})
        return await use_case_factory(uow)

    _run.calls = []
    return _run


class TestUpdateRunStatus:
    async def test_transitions_through_run_repository(self) -> None:
        uow = make_mock_uow()
        repo = AsyncMock()
        repo.update_run_status.return_value = True
        uow.get_workflow_run_repository = MagicMock(return_value=repo)
        run_id = uuid4()
        runner = _fake_runner(uow)

        with patch.object(rl, "execute_use_case", runner):
            result = await rl.update_run_status(run_id, "completed", error_message=None)

        assert result is True
        repo.update_run_status.assert_awaited_once_with(
            run_id, "completed", error_message=None
        )

    async def test_runs_without_rollback(self) -> None:
        """The terminal write happens during failure teardown — see the module docstring."""
        uow = make_mock_uow()
        uow.get_workflow_run_repository = MagicMock(return_value=AsyncMock())
        runner = _fake_runner(uow)

        with patch.object(rl, "execute_use_case", runner):
            await rl.update_run_status(uuid4(), "failed")

        assert runner.calls == [{"user_id": None, "rollback": False}]


class TestUpdateNodeStatus:
    async def test_forwards_node_detail_kwargs(self) -> None:
        uow = make_mock_uow()
        repo = AsyncMock()
        uow.get_workflow_run_repository = MagicMock(return_value=repo)
        run_id = uuid4()

        with patch.object(rl, "execute_use_case", _fake_runner(uow)):
            await rl.update_node_status(
                run_id, "node-1", "running", input_track_count=7
            )

        assert repo.update_node_status.await_args.args == (
            run_id,
            "node-1",
            "running",
        )
        assert repo.update_node_status.await_args.kwargs["input_track_count"] == 7


class TestBumpHeartbeat:
    async def test_bumps_via_run_repository(self) -> None:
        uow = make_mock_uow()
        repo = AsyncMock()
        uow.get_workflow_run_repository = MagicMock(return_value=repo)
        run_id = uuid4()

        with patch.object(rl, "execute_use_case", _fake_runner(uow)):
            await rl.bump_heartbeat(run_id)

        repo.bump_heartbeat.assert_awaited_once_with(run_id)

    async def test_suppresses_errors(self) -> None:
        """Heartbeats are advisory — a DB blip during a bump must not propagate."""
        run_id = uuid4()

        with patch.object(rl, "execute_use_case", side_effect=RuntimeError("db down")):
            await rl.bump_heartbeat(run_id)  # must not raise

    async def test_does_not_suppress_cancellation(self) -> None:
        """Cancellation must still tear the ticker down, not be swallowed as a blip."""
        with patch.object(rl, "execute_use_case", side_effect=RuntimeError("db down")):
            await rl.bump_heartbeat(uuid4())

        with (
            patch.object(rl, "execute_use_case", side_effect=BaseException("stop")),
            pytest.raises(BaseException, match="stop"),
        ):
            await rl.bump_heartbeat(uuid4())
