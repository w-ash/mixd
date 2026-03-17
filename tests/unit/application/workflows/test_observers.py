"""Tests for NodeExecutionObserver implementations.

Validates ProgressNodeObserver emits correct progress events,
RunHistoryObserver persists to DB + pushes SSE events,
and NullNodeObserver is a safe no-op.
"""

import asyncio
from unittest.mock import AsyncMock

import pytest

from src.application.workflows.observers import (
    CompositeNodeObserver,
    NullNodeObserver,
    ProgressNodeObserver,
    RunHistoryObserver,
)
from src.domain.entities.progress import ProgressStatus
from src.domain.entities.track import Artist, Track, TrackList
from src.domain.entities.workflow import (
    NodeExecutionEvent,
    WorkflowTaskDef,
)


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


class TestRunHistoryObserver:
    """Tests for RunHistoryObserver DB persistence + SSE push."""

    async def test_on_node_starting_updates_db_and_pushes_sse(self, task_def):
        """Starting a node calls the injected updater and pushes node_status SSE event."""
        queue: asyncio.Queue = asyncio.Queue()
        mock_updater = AsyncMock()

        observer = RunHistoryObserver(
            run_id=10, update_node_status=mock_updater, sse_queue=queue
        )
        event = NodeExecutionEvent(task_def=task_def, execution_order=2, total_nodes=5)

        await observer.on_node_starting(event)

        # Verify injected updater was called
        mock_updater.assert_called_once()
        call_kwargs = mock_updater.call_args[1]
        assert call_kwargs["run_id"] == 10
        assert call_kwargs["node_id"] == "enrich_1"
        assert call_kwargs["status"] == "running"

        # Verify SSE event
        assert not queue.empty()
        sse = await queue.get()
        assert sse["event"] == "node_status"
        assert sse["data"]["run_id"] == 10
        assert sse["data"]["node_id"] == "enrich_1"
        assert sse["data"]["status"] == "running"

    async def test_on_node_completed_persists_node_details(
        self, task_def, sample_result
    ):
        """Node details from the result are passed through to the updater."""
        mock_updater = AsyncMock()
        observer = RunHistoryObserver(run_id=10, update_node_status=mock_updater)
        result_with_details = {
            "tracklist": sample_result["tracklist"],
            "node_details": {
                "playlist_changes": {
                    "tracks_added": [
                        {"track_id": 1, "title": "Track A", "artists": "Artist 1"}
                    ],
                    "tracks_removed": [
                        {"track_id": 2, "title": "Track B", "artists": "Artist 2"}
                    ],
                    "tracks_moved": 0,
                    "playlist_id": "test-playlist",
                    "connector": "spotify",
                },
            },
        }
        event = NodeExecutionEvent(
            task_def=task_def,
            execution_order=1,
            total_nodes=3,
            duration_ms=100,
            output_track_count=1,
        )

        await observer.on_node_completed(event, result_with_details)

        call_kwargs = mock_updater.call_args[1]
        assert call_kwargs["node_details"] is not None
        changes = call_kwargs["node_details"]["playlist_changes"]
        assert len(changes["tracks_added"]) == 1
        assert len(changes["tracks_removed"]) == 1
        assert changes["playlist_id"] == "test-playlist"

    async def test_on_node_completed_pushes_sse_with_counts(
        self, task_def, sample_result
    ):
        """Completed node pushes SSE with duration and track counts."""
        queue: asyncio.Queue = asyncio.Queue()
        mock_updater = AsyncMock()

        observer = RunHistoryObserver(
            run_id=10, update_node_status=mock_updater, sse_queue=queue
        )
        event = NodeExecutionEvent(
            task_def=task_def,
            execution_order=2,
            total_nodes=5,
            duration_ms=1500,
            input_track_count=100,
            output_track_count=42,
        )

        await observer.on_node_completed(event, sample_result)

        sse = await queue.get()
        assert sse["data"]["status"] == "completed"
        assert sse["data"]["duration_ms"] == 1500
        assert sse["data"]["output_track_count"] == 42

    async def test_on_node_failed_includes_error_message(self, task_def):
        """Failed node SSE event includes error_message."""
        queue: asyncio.Queue = asyncio.Queue()
        mock_updater = AsyncMock()

        observer = RunHistoryObserver(
            run_id=10, update_node_status=mock_updater, sse_queue=queue
        )
        event = NodeExecutionEvent(task_def=task_def, execution_order=1, total_nodes=3)

        await observer.on_node_failed(event, RuntimeError("API timeout"))

        sse = await queue.get()
        assert sse["data"]["status"] == "failed"
        assert sse["data"]["error_message"] == "API timeout"

    async def test_no_queue_means_no_sse(self, task_def, sample_result):
        """Without a queue, SSE push is silently skipped."""
        mock_updater = AsyncMock()

        observer = RunHistoryObserver(
            run_id=10, update_node_status=mock_updater, sse_queue=None
        )
        event = NodeExecutionEvent(task_def=task_def, execution_order=1, total_nodes=1)
        # Should not raise
        await observer.on_node_completed(event, sample_result)

    async def test_db_failure_does_not_propagate(self, task_def, sample_result):
        """If DB write fails, the observer logs and continues — never crashes the workflow."""
        queue: asyncio.Queue = asyncio.Queue()
        mock_updater = AsyncMock(side_effect=RuntimeError("DB gone"))

        observer = RunHistoryObserver(
            run_id=10, update_node_status=mock_updater, sse_queue=queue
        )
        event = NodeExecutionEvent(task_def=task_def, execution_order=1, total_nodes=1)

        # Should NOT raise even though DB updater fails
        await observer.on_node_completed(event, sample_result)

        # SSE event should still be pushed even if DB failed
        assert not queue.empty()

    async def test_persist_failure_count_starts_at_zero(self):
        """New observer has zero persistence failures."""
        observer = RunHistoryObserver(run_id=10, update_node_status=AsyncMock())
        assert observer.persist_failure_count == 0

    async def test_persist_failure_count_increments_on_db_error(
        self, task_def, sample_result
    ):
        """Each DB failure increments persist_failure_count."""
        mock_updater = AsyncMock(side_effect=RuntimeError("DB gone"))
        observer = RunHistoryObserver(run_id=10, update_node_status=mock_updater)
        event = NodeExecutionEvent(task_def=task_def, execution_order=1, total_nodes=3)

        # Three lifecycle calls that each hit the updater
        await observer.on_node_starting(event)
        await observer.on_node_completed(event, sample_result)
        await observer.on_node_failed(event, ValueError("x"))

        assert observer.persist_failure_count == 3


class TestCompositeNodeObserver:
    """Tests for CompositeNodeObserver multi-observer delegation."""

    async def test_delegates_to_all_observers(self, task_def, sample_result):
        """All lifecycle methods are forwarded to every inner observer."""
        obs_a = AsyncMock()
        obs_b = AsyncMock()
        composite = CompositeNodeObserver([obs_a, obs_b])

        event = NodeExecutionEvent(
            task_def=task_def, execution_order=1, total_nodes=3, duration_ms=100
        )

        await composite.on_node_starting(event)
        obs_a.on_node_starting.assert_called_once_with(event)
        obs_b.on_node_starting.assert_called_once_with(event)

        await composite.on_node_completed(event, sample_result)
        obs_a.on_node_completed.assert_called_once_with(event, sample_result)
        obs_b.on_node_completed.assert_called_once_with(event, sample_result)

        error = ValueError("boom")
        await composite.on_node_failed(event, error)
        obs_a.on_node_failed.assert_called_once_with(event, error)
        obs_b.on_node_failed.assert_called_once_with(event, error)

    async def test_single_observer_still_works(self, task_def, sample_result):
        """Composite with one observer delegates correctly."""
        obs = AsyncMock()
        composite = CompositeNodeObserver([obs])
        event = NodeExecutionEvent(task_def=task_def, execution_order=1, total_nodes=1)

        await composite.on_node_starting(event)
        obs.on_node_starting.assert_called_once()


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
