"""Tests for NodeExecutionObserver implementations.

Validates ProgressNodeObserver emits correct progress events and
NullNodeObserver is a safe no-op.
"""

from unittest.mock import AsyncMock

import pytest

from src.application.workflows.observers import NullNodeObserver, ProgressNodeObserver
from src.domain.entities.progress import ProgressStatus
from src.domain.entities.track import Artist, Track, TrackList
from src.domain.entities.workflow import NodeExecutionEvent, WorkflowTaskDef


@pytest.fixture
def task_def():
    return WorkflowTaskDef(id="enrich_1", type="enricher.spotify")


@pytest.fixture
def sample_result():
    tracklist = TrackList(tracks=[Track(id=1, title="A", artists=[Artist(name="X")])])
    return {"tracklist": tracklist}


class TestProgressNodeObserver:
    """Tests for ProgressNodeObserver progress event emission."""

    async def test_on_node_completed_emits_event(self, task_def, sample_result):
        """Completed node emits IN_PROGRESS event with correct step/total."""
        pm = AsyncMock()
        observer = ProgressNodeObserver(pm, "op-123")
        event = NodeExecutionEvent(
            task_def=task_def,
            execution_order=2,
            total_nodes=5,
            duration_ms=150,
            input_track_count=10,
            output_track_count=1,
        )

        await observer.on_node_completed(event, sample_result)

        pm.emit_progress.assert_called_once()
        progress = pm.emit_progress.call_args[0][0]
        assert progress.operation_id == "op-123"
        assert progress.current == 2
        assert progress.total == 5
        assert "Enricher Spotify" in progress.message
        assert progress.status == ProgressStatus.IN_PROGRESS

    async def test_on_node_failed_emits_failure_event(self, task_def):
        """Failed node emits FAILED event with error message."""
        pm = AsyncMock()
        observer = ProgressNodeObserver(pm, "op-456")
        error = ValueError("bad config")
        event = NodeExecutionEvent(
            task_def=task_def,
            execution_order=1,
            total_nodes=3,
            duration_ms=50,
        )

        await observer.on_node_failed(event, error)

        pm.emit_progress.assert_called_once()
        progress = pm.emit_progress.call_args[0][0]
        assert progress.status == ProgressStatus.FAILED
        assert "bad config" in progress.message

    async def test_on_node_starting_is_noop(self, task_def):
        """Starting notification does not emit progress (bars show completion)."""
        pm = AsyncMock()
        observer = ProgressNodeObserver(pm, "op-789")
        event = NodeExecutionEvent(task_def=task_def, execution_order=1, total_nodes=3)

        await observer.on_node_starting(event)

        pm.emit_progress.assert_not_called()


class TestNullNodeObserver:
    """Tests for NullNodeObserver no-op behavior."""

    async def test_all_methods_are_noop(self, task_def, sample_result):
        """NullNodeObserver methods complete without error."""
        observer = NullNodeObserver()
        event = NodeExecutionEvent(
            task_def=task_def, execution_order=1, total_nodes=3, duration_ms=100
        )

        await observer.on_node_starting(event)
        await observer.on_node_completed(event, sample_result)
        await observer.on_node_failed(event, ValueError("x"))
