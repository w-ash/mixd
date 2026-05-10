"""Tests for the in-process workflow concurrency guard."""

import pytest

from src.application.workflows.run_guard import (
    WorkflowAlreadyRunningError,
    _running_lock,
    _running_workflows,
    acquire_workflow_slot,
    is_workflow_running,
    release_workflow_slot,
)


class TestExecutionGuard:
    """Tests for the per-workflow concurrency guard."""

    @pytest.fixture(autouse=True)
    async def _clean_guard(self):
        """Ensure guard state is clean before and after each test."""
        _running_workflows.clear()
        yield
        _running_workflows.clear()

    async def test_is_workflow_running_false_when_idle(self):
        """No workflows running returns False."""
        assert await is_workflow_running("wf-1") is False

    async def test_is_workflow_running_true_when_active(self):
        """Manually marked workflow shows as running."""
        async with _running_lock:
            _running_workflows.add("wf-1")
        assert await is_workflow_running("wf-1") is True

    async def test_already_running_error_has_workflow_id(self):
        """WorkflowAlreadyRunningError carries the workflow ID."""
        err = WorkflowAlreadyRunningError("wf-42")
        assert err.workflow_id == "wf-42"
        assert "wf-42" in str(err)

    async def test_acquire_then_release_round_trip(self):
        """Acquire reserves the slot; release frees it."""
        await acquire_workflow_slot("wf-7")
        assert await is_workflow_running("wf-7") is True
        await release_workflow_slot("wf-7")
        assert await is_workflow_running("wf-7") is False

    async def test_acquire_raises_when_already_held(self):
        """Second acquire of the same id raises WorkflowAlreadyRunningError."""
        await acquire_workflow_slot("wf-9")
        with pytest.raises(WorkflowAlreadyRunningError) as excinfo:
            await acquire_workflow_slot("wf-9")
        assert excinfo.value.workflow_id == "wf-9"
        await release_workflow_slot("wf-9")

    async def test_release_unknown_id_is_noop(self):
        """Releasing an id that was never acquired must not raise."""
        await release_workflow_slot("never-held")
