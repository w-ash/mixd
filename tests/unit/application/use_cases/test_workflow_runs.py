"""Unit tests for workflow run use cases.

Tests RunWorkflowUseCase (create pending run), ListWorkflowRunsUseCase,
GetWorkflowRunUseCase, GetLatestWorkflowRunsUseCase, and
ExecuteWorkflowRunUseCase lifecycle, exception handling, and correlation context.
"""

from asyncio import CancelledError
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from src.application.use_cases.workflow_runs import (
    ExecuteWorkflowRunUseCase,
    GetLatestWorkflowRunsCommand,
    GetLatestWorkflowRunsUseCase,
    GetWorkflowRunCommand,
    GetWorkflowRunUseCase,
    ListWorkflowRunsCommand,
    ListWorkflowRunsUseCase,
    RunWorkflowCommand,
    RunWorkflowUseCase,
    _serialize_output_tracks,
)
from src.application.workflows.prefect import WorkflowAlreadyRunningError
from src.config.constants import WorkflowConstants
from src.domain.entities.workflow import WorkflowRun
from src.domain.exceptions import NotFoundError
from tests.fixtures import make_mock_uow, make_tracks, make_workflow, make_workflow_def


@contextmanager
def _patch_execute_deps(*, mock_run_return=None, observer_persist_failures=0):
    """Patch the dependencies that ExecuteWorkflowRunUseCase imports at call time.

    Yields (mock_logger, mock_run_workflow, mock_observer) for test assertions.
    """
    mock_observer = MagicMock()
    mock_observer.persist_failure_count = observer_persist_failures

    with (
        patch("src.application.use_cases.workflow_runs.logger") as mock_logger,
        patch("src.application.services.progress_manager.get_progress_manager"),
        patch(
            "src.application.workflows.observers.RunHistoryObserver",
            return_value=mock_observer,
        ),
        patch(
            "src.application.workflows.prefect.run_workflow",
            new_callable=AsyncMock,
        ) as mock_run,
    ):
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=None)
        mock_ctx.__exit__ = MagicMock(return_value=False)
        mock_logger.contextualize.return_value = mock_ctx
        mock_logger.bind.return_value = mock_logger

        if mock_run_return is not None:
            mock_run.return_value = mock_run_return
        else:
            mock_run.return_value = MagicMock(tracks=[])

        yield mock_logger, mock_run, mock_observer


class TestRunWorkflowUseCase:
    """RunWorkflowUseCase creates a PENDING run record."""

    @pytest.fixture
    def workflow(self):
        return make_workflow(id=1)

    async def test_creates_pending_run(self, workflow) -> None:
        run_repo = AsyncMock()
        run_repo.create_run.side_effect = lambda r: WorkflowRun(
            id=42,
            workflow_id=r.workflow_id,
            status=r.status,
            definition_snapshot=r.definition_snapshot,
            nodes=r.nodes,
        )
        wf_repo = AsyncMock()
        wf_repo.get_workflow_by_id.return_value = workflow
        uow = make_mock_uow(workflow_repo=wf_repo, workflow_run_repo=run_repo)

        with patch(
            "src.application.use_cases.workflow_runs.is_workflow_running",
            new_callable=AsyncMock,
            return_value=False,
        ):
            result = await RunWorkflowUseCase().execute(
                RunWorkflowCommand(workflow_id=1), uow
            )

        assert result.run_id == 42
        assert result.workflow is workflow

        # Verify run was created with correct structure
        created_run = run_repo.create_run.call_args[0][0]
        assert created_run.status == "pending"
        assert created_run.workflow_id == 1
        assert len(created_run.nodes) == len(workflow.definition.tasks)

    async def test_copies_definition_version_to_run(self) -> None:
        """Run record captures the workflow's current definition_version."""
        workflow = make_workflow(id=1, definition_version=7)
        run_repo = AsyncMock()
        run_repo.create_run.side_effect = lambda r: WorkflowRun(
            id=42,
            workflow_id=r.workflow_id,
            status=r.status,
            definition_snapshot=r.definition_snapshot,
            definition_version=r.definition_version,
            nodes=r.nodes,
        )
        wf_repo = AsyncMock()
        wf_repo.get_workflow_by_id.return_value = workflow
        uow = make_mock_uow(workflow_repo=wf_repo, workflow_run_repo=run_repo)

        with patch(
            "src.application.use_cases.workflow_runs.is_workflow_running",
            new_callable=AsyncMock,
            return_value=False,
        ):
            result = await RunWorkflowUseCase().execute(
                RunWorkflowCommand(workflow_id=1), uow
            )

        assert result.run_id == 42
        created_run = run_repo.create_run.call_args[0][0]
        assert created_run.definition_version == 7

    async def test_rejects_already_running(self, workflow) -> None:
        wf_repo = AsyncMock()
        wf_repo.get_workflow_by_id.return_value = workflow
        uow = make_mock_uow(workflow_repo=wf_repo)

        with (
            patch(
                "src.application.use_cases.workflow_runs.is_workflow_running",
                new_callable=AsyncMock,
                return_value=True,
            ),
            pytest.raises(WorkflowAlreadyRunningError),
        ):
            await RunWorkflowUseCase().execute(RunWorkflowCommand(workflow_id=1), uow)

    async def test_workflow_not_found(self) -> None:
        wf_repo = AsyncMock()
        wf_repo.get_workflow_by_id.side_effect = NotFoundError("not found")
        uow = make_mock_uow(workflow_repo=wf_repo)

        with pytest.raises(NotFoundError):
            await RunWorkflowUseCase().execute(RunWorkflowCommand(workflow_id=999), uow)


class TestListWorkflowRunsUseCase:
    """ListWorkflowRunsUseCase returns paginated run list."""

    async def test_returns_runs(self) -> None:
        workflow = make_workflow(id=1)
        runs = [
            WorkflowRun(id=1, workflow_id=1, status="completed"),
            WorkflowRun(id=2, workflow_id=1, status="pending"),
        ]
        wf_repo = AsyncMock()
        wf_repo.get_workflow_by_id.return_value = workflow
        run_repo = AsyncMock()
        run_repo.get_runs_for_workflow.return_value = (runs, 2)
        uow = make_mock_uow(workflow_repo=wf_repo, workflow_run_repo=run_repo)

        result = await ListWorkflowRunsUseCase().execute(
            ListWorkflowRunsCommand(workflow_id=1), uow
        )

        assert result.total_count == 2
        assert len(result.runs) == 2
        assert result.runs[0].status == "completed"

    async def test_workflow_not_found(self) -> None:
        wf_repo = AsyncMock()
        wf_repo.get_workflow_by_id.side_effect = NotFoundError("not found")
        uow = make_mock_uow(workflow_repo=wf_repo)

        with pytest.raises(NotFoundError):
            await ListWorkflowRunsUseCase().execute(
                ListWorkflowRunsCommand(workflow_id=999), uow
            )


class TestGetWorkflowRunUseCase:
    """GetWorkflowRunUseCase returns a run with nodes."""

    async def test_returns_run(self) -> None:
        workflow = make_workflow(id=1)
        run = WorkflowRun(id=5, workflow_id=1, status="completed")
        wf_repo = AsyncMock()
        wf_repo.get_workflow_by_id.return_value = workflow
        run_repo = AsyncMock()
        run_repo.get_run_by_id.return_value = run
        uow = make_mock_uow(workflow_repo=wf_repo, workflow_run_repo=run_repo)

        result = await GetWorkflowRunUseCase().execute(
            GetWorkflowRunCommand(workflow_id=1, run_id=5), uow
        )

        assert result.run.id == 5
        assert result.run.status == "completed"

    async def test_run_not_found(self) -> None:
        workflow = make_workflow(id=1)
        wf_repo = AsyncMock()
        wf_repo.get_workflow_by_id.return_value = workflow
        run_repo = AsyncMock()
        run_repo.get_run_by_id.side_effect = NotFoundError("not found")
        uow = make_mock_uow(workflow_repo=wf_repo, workflow_run_repo=run_repo)

        with pytest.raises(NotFoundError):
            await GetWorkflowRunUseCase().execute(
                GetWorkflowRunCommand(workflow_id=1, run_id=999), uow
            )

    async def test_run_belongs_to_wrong_workflow(self) -> None:
        """Prevents accessing run 5 of workflow 2 via /workflows/1/runs/5."""
        workflow = make_workflow(id=1)
        run = WorkflowRun(id=5, workflow_id=2, status="completed")
        wf_repo = AsyncMock()
        wf_repo.get_workflow_by_id.return_value = workflow
        run_repo = AsyncMock()
        run_repo.get_run_by_id.return_value = run
        uow = make_mock_uow(workflow_repo=wf_repo, workflow_run_repo=run_repo)

        with pytest.raises(NotFoundError, match="does not belong"):
            await GetWorkflowRunUseCase().execute(
                GetWorkflowRunCommand(workflow_id=1, run_id=5), uow
            )


class TestGetLatestWorkflowRunsUseCase:
    """GetLatestWorkflowRunsUseCase batch-fetches latest runs."""

    async def test_returns_latest_runs(self) -> None:
        run_a = WorkflowRun(id=10, workflow_id=1, status="completed")
        run_b = WorkflowRun(id=20, workflow_id=2, status="pending")
        run_repo = AsyncMock()
        run_repo.get_latest_runs_for_workflows.return_value = {1: run_a, 2: run_b}
        uow = make_mock_uow(workflow_run_repo=run_repo)

        command = GetLatestWorkflowRunsCommand(workflow_ids=[1, 2])
        result = await GetLatestWorkflowRunsUseCase().execute(command, uow)

        assert len(result.latest_runs) == 2
        assert result.latest_runs[1].status == "completed"
        assert result.latest_runs[2].status == "pending"
        run_repo.get_latest_runs_for_workflows.assert_called_once_with([1, 2])

    async def test_returns_empty_when_no_runs(self) -> None:
        run_repo = AsyncMock()
        run_repo.get_latest_runs_for_workflows.return_value = {}
        uow = make_mock_uow(workflow_run_repo=run_repo)

        command = GetLatestWorkflowRunsCommand(workflow_ids=[1, 2])
        result = await GetLatestWorkflowRunsUseCase().execute(command, uow)

        assert result.latest_runs == {}


class TestSerializeOutputTracks:
    """_serialize_output_tracks produces lightweight dicts for the run record."""

    def test_serializes_tracks_with_rank(self) -> None:
        tracks = make_tracks(count=3)
        result = _serialize_output_tracks(tracks)

        assert len(result) == 3
        assert result[0]["rank"] == 1
        assert result[1]["rank"] == 2
        assert result[2]["rank"] == 3
        assert result[0]["track_id"] == tracks[0].id
        assert result[0]["title"] == tracks[0].title
        assert isinstance(result[0]["artists"], str)

    def test_empty_list(self) -> None:
        assert _serialize_output_tracks([]) == []


class TestExecuteWorkflowRunUseCase:
    """ExecuteWorkflowRunUseCase lifecycle, exception handling, and diagnostics."""

    async def test_execute_updates_status_to_running_then_completed(self) -> None:
        """Happy path: RUNNING → COMPLETED with duration_ms + output_track_count + output_tracks."""
        workflow_def = make_workflow_def()
        mock_updater = AsyncMock()
        use_case = ExecuteWorkflowRunUseCase(
            update_run_status=mock_updater, update_node_status=AsyncMock()
        )

        tracks = make_tracks(count=3)
        with _patch_execute_deps(mock_run_return=MagicMock(tracks=tracks)):
            result = await use_case.execute(workflow_def, run_id=7)

        # First call: RUNNING with started_at
        assert mock_updater.call_count == 2
        running_call = mock_updater.call_args_list[0]
        assert running_call == call(
            7,
            WorkflowConstants.RUN_STATUS_RUNNING,
            started_at=running_call.kwargs["started_at"],
        )
        assert running_call.kwargs["started_at"] is not None

        # Second call: COMPLETED with completed_at, duration_ms, output_track_count, output_tracks
        completed_call = mock_updater.call_args_list[1]
        assert completed_call.args[1] == WorkflowConstants.RUN_STATUS_COMPLETED
        assert completed_call.kwargs["completed_at"] is not None
        assert completed_call.kwargs["duration_ms"] >= 0
        assert completed_call.kwargs["output_track_count"] == 3
        assert len(completed_call.kwargs["output_tracks"]) == 3
        assert completed_call.kwargs["output_tracks"][0]["rank"] == 1

        # Result object
        assert result.status == WorkflowConstants.RUN_STATUS_COMPLETED
        assert result.run_id == 7
        assert result.output_track_count == 3
        assert result.duration_ms >= 0

    async def test_execute_handles_general_exception(self) -> None:
        """RuntimeError during execution → FAILED status with truncated message."""
        workflow_def = make_workflow_def()
        mock_updater = AsyncMock()
        use_case = ExecuteWorkflowRunUseCase(
            update_run_status=mock_updater, update_node_status=AsyncMock()
        )

        with _patch_execute_deps() as (_logger, mock_run, _observer):
            mock_run.side_effect = RuntimeError("API timeout")
            result = await use_case.execute(workflow_def, run_id=10)

        # Status updater called: RUNNING, then FAILED
        assert mock_updater.call_count == 2
        failed_call = mock_updater.call_args_list[1]
        assert failed_call.args[1] == WorkflowConstants.RUN_STATUS_FAILED
        assert failed_call.kwargs["error_message"] == "API timeout"

        assert result.status == WorkflowConstants.RUN_STATUS_FAILED
        assert result.error_message == "API timeout"

    async def test_execute_truncates_long_error_message(self) -> None:
        """Error messages exceeding ERROR_MESSAGE_MAX_LENGTH are truncated."""
        workflow_def = make_workflow_def()
        mock_updater = AsyncMock()
        use_case = ExecuteWorkflowRunUseCase(
            update_run_status=mock_updater, update_node_status=AsyncMock()
        )

        long_msg = "x" * (WorkflowConstants.ERROR_MESSAGE_MAX_LENGTH + 500)
        with _patch_execute_deps() as (_logger, mock_run, _observer):
            mock_run.side_effect = RuntimeError(long_msg)
            result = await use_case.execute(workflow_def, run_id=11)

        assert len(result.error_message) == WorkflowConstants.ERROR_MESSAGE_MAX_LENGTH

    async def test_execute_handles_cancelled_error(self) -> None:
        """CancelledError → FAILED with CANCELLED_BY_SERVER_MESSAGE, then re-raised."""
        workflow_def = make_workflow_def()
        mock_updater = AsyncMock()
        use_case = ExecuteWorkflowRunUseCase(
            update_run_status=mock_updater, update_node_status=AsyncMock()
        )

        with _patch_execute_deps() as (_logger, mock_run, _observer):
            mock_run.side_effect = CancelledError()
            with pytest.raises(CancelledError):
                await use_case.execute(workflow_def, run_id=12)

        # Status updater called: RUNNING, then FAILED with cancellation message
        assert mock_updater.call_count == 2
        failed_call = mock_updater.call_args_list[1]
        assert failed_call.args[1] == WorkflowConstants.RUN_STATUS_FAILED
        assert (
            failed_call.kwargs["error_message"]
            == WorkflowConstants.CANCELLED_BY_SERVER_MESSAGE
        )

    async def test_execute_logs_when_status_update_fails_on_exception(self) -> None:
        """If both run_workflow and update_run_status fail, result is still returned."""
        workflow_def = make_workflow_def()
        mock_updater = AsyncMock()
        # First call (RUNNING) succeeds, second call (FAILED) raises
        mock_updater.side_effect = [None, RuntimeError("DB connection lost")]
        use_case = ExecuteWorkflowRunUseCase(
            update_run_status=mock_updater, update_node_status=AsyncMock()
        )

        with _patch_execute_deps() as (mock_logger, mock_run, _observer):
            mock_run.side_effect = RuntimeError("API timeout")
            result = await use_case.execute(workflow_def, run_id=13)

        # Despite the double failure, a result is returned (not crashed)
        assert result.status == WorkflowConstants.RUN_STATUS_FAILED
        assert result.error_message == "API timeout"

        # The failure to update status was logged
        mock_logger.opt.return_value.error.assert_any_call(
            "Failed to update run status to FAILED"
        )

    async def test_contextualize_binds_workflow_and_run_ids(self) -> None:
        """logger.contextualize is called with workflow_id, workflow_name, run_id."""
        workflow_def = make_workflow_def(id="wf-ctx-test", name="Context Test")
        use_case = ExecuteWorkflowRunUseCase(
            update_run_status=AsyncMock(), update_node_status=AsyncMock()
        )

        with _patch_execute_deps() as (mock_logger, _mock_run, _observer):
            await use_case.execute(workflow_def, run_id=99)

        mock_logger.contextualize.assert_called_once_with(
            workflow_id="wf-ctx-test",
            workflow_name="Context Test",
            run_id=99,
        )

    async def test_logs_observer_persist_failures(self) -> None:
        """Logs error when observer has persist_failure_count > 0."""
        workflow_def = make_workflow_def()
        use_case = ExecuteWorkflowRunUseCase(
            update_run_status=AsyncMock(), update_node_status=AsyncMock()
        )

        with _patch_execute_deps(observer_persist_failures=2) as (
            mock_logger,
            _mock_run,
            _observer,
        ):
            await use_case.execute(workflow_def, run_id=42)

        mock_logger.error.assert_any_call(
            "Run history incomplete — DB persistence failures during execution",
            persist_failures=2,
        )
