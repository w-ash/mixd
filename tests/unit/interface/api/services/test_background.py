"""Tests for background task launcher and done-callback enrichment.

Validates _on_task_done logs with workflow_id, run_id, and duration_ms
when task metadata is registered via launch_background.
"""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from src.interface.api.services.background import (
    _background_tasks,
    _on_task_done,
    _task_meta,
    _TaskMeta,
    launch_background,
)


@pytest.fixture(autouse=True)
def _clean_state():
    """Ensure module state is clean before and after each test."""
    _background_tasks.clear()
    _task_meta.clear()
    yield
    _background_tasks.clear()
    _task_meta.clear()


class TestOnTaskDone:
    """Tests for _on_task_done callback with enriched metadata."""

    def test_logs_with_metadata_on_success(self) -> None:
        """Completed task logs workflow_id, run_id, and duration_ms."""
        task = MagicMock(spec=asyncio.Task)
        task.get_name.return_value = "workflow_run_abc"
        task.cancelled.return_value = False
        task.exception.return_value = None

        _task_meta["workflow_run_abc"] = _TaskMeta(
            workflow_id="wf-1",
            run_id=42,
            started_at_ns=0,  # duration will be large, just check key exists
        )
        _background_tasks.add(task)

        with patch("src.interface.api.services.background.logger") as mock_logger:
            mock_logger.bind.return_value = mock_logger
            _on_task_done(task)

        mock_logger.info.assert_called_once()
        call_kwargs = mock_logger.info.call_args
        assert call_kwargs.kwargs["workflow_id"] == "wf-1"
        assert call_kwargs.kwargs["run_id"] == 42
        assert "duration_ms" in call_kwargs.kwargs

    def test_logs_without_metadata_on_success(self) -> None:
        """Tasks without metadata still log task_name."""
        task = MagicMock(spec=asyncio.Task)
        task.get_name.return_value = "import_task_xyz"
        task.cancelled.return_value = False
        task.exception.return_value = None
        _background_tasks.add(task)

        with patch("src.interface.api.services.background.logger") as mock_logger:
            mock_logger.bind.return_value = mock_logger
            _on_task_done(task)

        mock_logger.info.assert_called_once()
        call_kwargs = mock_logger.info.call_args
        assert call_kwargs.kwargs["task_name"] == "import_task_xyz"
        assert "workflow_id" not in call_kwargs.kwargs

    def test_logs_with_metadata_on_failure(self) -> None:
        """Failed task logs with workflow context and exception."""
        task = MagicMock(spec=asyncio.Task)
        task.get_name.return_value = "workflow_run_fail"
        task.cancelled.return_value = False
        exc = RuntimeError("boom")
        task.exception.return_value = exc
        _background_tasks.add(task)

        _task_meta["workflow_run_fail"] = _TaskMeta(
            workflow_id="wf-2", run_id=7, started_at_ns=0
        )

        with patch("src.interface.api.services.background.logger") as mock_logger:
            mock_logger.bind.return_value = mock_logger
            mock_logger.opt.return_value = mock_logger
            _on_task_done(task)

        mock_logger.opt.assert_called_once_with(exception=exc)
        call_kwargs = mock_logger.error.call_args
        assert call_kwargs.kwargs["workflow_id"] == "wf-2"

    def test_metadata_cleaned_up_after_done(self) -> None:
        """Task metadata is removed from _task_meta after callback."""
        task = MagicMock(spec=asyncio.Task)
        task.get_name.return_value = "cleanup_test"
        task.cancelled.return_value = False
        task.exception.return_value = None
        _background_tasks.add(task)

        _task_meta["cleanup_test"] = _TaskMeta(
            workflow_id="wf-3", run_id=1, started_at_ns=0
        )

        with patch("src.interface.api.services.background.logger") as mock_logger:
            mock_logger.bind.return_value = mock_logger
            _on_task_done(task)

        assert "cleanup_test" not in _task_meta


class TestLaunchBackground:
    """Tests for launch_background metadata registration."""

    def test_registers_metadata_when_ids_provided(self) -> None:
        """workflow_id + run_id stores _TaskMeta keyed by task name."""
        mock_task = MagicMock(spec=asyncio.Task)

        with patch(
            "src.interface.api.services.background.asyncio.create_task",
            return_value=mock_task,
        ):
            launch_background(
                "wf_run_test",
                MagicMock(return_value=MagicMock()),
                workflow_id="wf-10",
                run_id=55,
            )

        assert "wf_run_test" in _task_meta
        assert _task_meta["wf_run_test"].workflow_id == "wf-10"
        assert _task_meta["wf_run_test"].run_id == 55

    def test_no_metadata_when_ids_omitted(self) -> None:
        """Without workflow_id/run_id, no metadata is stored."""
        mock_task = MagicMock(spec=asyncio.Task)

        with patch(
            "src.interface.api.services.background.asyncio.create_task",
            return_value=mock_task,
        ):
            launch_background("plain_task", MagicMock(return_value=MagicMock()))

        assert "plain_task" not in _task_meta
