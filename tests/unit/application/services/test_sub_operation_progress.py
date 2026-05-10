"""Unit tests for the sub-operation progress bridge.

Tests create_sub_operation, complete_sub_operation, and emit_phase_progress
functions that bridge infrastructure callbacks to AsyncProgressManager.
"""

import asyncio
from unittest.mock import AsyncMock

from src.application.services.sub_operation_progress import (
    complete_sub_operation,
    create_sub_operation,
    create_throttled_sub_operation,
    emit_phase_progress,
)
from src.domain.entities.progress import OperationStatus


def _make_mock_manager() -> AsyncMock:
    """Build a mock AsyncProgressManager with the required methods."""
    manager = AsyncMock()
    manager.start_operation = AsyncMock(return_value="sub-op-123")
    manager.emit_progress = AsyncMock()
    manager.complete_operation = AsyncMock()
    return manager


class TestCreateSubOperationHappyPath:
    """Tests that create_sub_operation correctly starts an operation and returns a callback."""

    async def test_create_sub_operation_starts_operation(self):
        mock_manager = _make_mock_manager()

        sub_op_id, _callback = await create_sub_operation(
            progress_manager=mock_manager,
            description="Fetching lastfm metadata",
            total_items=50,
            parent_operation_id="parent-op-1",
            phase="enrich",
            node_type="enricher",
        )

        assert sub_op_id == "sub-op-123"
        mock_manager.start_operation.assert_awaited_once()

        # Verify the operation passed to start_operation has correct metadata
        created_op = mock_manager.start_operation.call_args[0][0]
        assert created_op.description == "Fetching lastfm metadata"
        assert created_op.total_items == 50
        assert created_op.metadata["parent_operation_id"] == "parent-op-1"
        assert created_op.metadata["phase"] == "enrich"
        assert created_op.metadata["node_type"] == "enricher"

    async def test_create_sub_operation_callback_emits_events(self):
        mock_manager = _make_mock_manager()

        _sub_op_id, callback = await create_sub_operation(
            progress_manager=mock_manager,
            description="Fetching metadata",
            total_items=10,
            parent_operation_id="parent-1",
            phase="fetch",
            node_type="source",
        )

        # Invoke the callback
        await callback(5, 10, "Processed 5/10")

        mock_manager.emit_progress.assert_awaited_once()
        emitted_event = mock_manager.emit_progress.call_args[0][0]
        assert emitted_event.operation_id == "sub-op-123"
        assert emitted_event.current == 5
        assert emitted_event.total == 10
        assert emitted_event.message == "Processed 5/10"


class TestCompleteSubOperation:
    """Tests that complete_sub_operation delegates correctly."""

    async def test_complete_sub_operation_completes(self):
        mock_manager = _make_mock_manager()

        await complete_sub_operation(mock_manager, "sub-op-123")

        mock_manager.complete_operation.assert_awaited_once_with(
            "sub-op-123", OperationStatus.COMPLETED
        )

    async def test_complete_sub_operation_with_failed_status(self):
        mock_manager = _make_mock_manager()

        await complete_sub_operation(mock_manager, "sub-op-456", OperationStatus.FAILED)

        mock_manager.complete_operation.assert_awaited_once_with(
            "sub-op-456", OperationStatus.FAILED
        )


class TestCreateThrottledSubOperation:
    """Throttled callback tests — verify rate cap, tail flush, and cleanup.

    Use a small min_interval_seconds (10ms) instead of mocking time so the
    tests exercise the real asyncio.sleep + asyncio.create_task path.
    """

    async def test_first_call_emits_immediately(self):
        mock_manager = _make_mock_manager()

        emitter = await create_throttled_sub_operation(
            progress_manager=mock_manager,
            description="Fetching lastfm metadata",
            total_items=100,
            parent_operation_id="parent-1",
            phase="enrich",
            node_type="enricher",
            min_interval_seconds=0.01,
        )

        await emitter(1, 100, "1/100")
        mock_manager.emit_progress.assert_awaited_once()

    async def test_terminal_tick_always_emits(self):
        """emitter(N, N, ...) bypasses the throttle and emits immediately."""
        mock_manager = _make_mock_manager()

        emitter = await create_throttled_sub_operation(
            progress_manager=mock_manager,
            description="x",
            total_items=10,
            parent_operation_id="p",
            phase="enrich",
            node_type="enricher",
            min_interval_seconds=10.0,  # huge window — only terminal would emit
        )

        await emitter(1, 10, "1/10")  # first call always emits
        await emitter(2, 10, "2/10")  # within window — suppressed
        await emitter(10, 10, "10/10")  # terminal — emits despite window

        # Two emits: the first call and the terminal tick
        assert mock_manager.emit_progress.await_count == 2
        last_event = mock_manager.emit_progress.call_args[0][0]
        assert last_event.current == 10
        assert last_event.total == 10

    async def test_rapid_calls_within_window_suppressed_then_tail_flushed(self):
        """High-frequency invocation collapses to <= 1 emit per window plus a tail."""
        mock_manager = _make_mock_manager()

        emitter = await create_throttled_sub_operation(
            progress_manager=mock_manager,
            description="x",
            total_items=1000,
            parent_operation_id="p",
            phase="enrich",
            node_type="enricher",
            min_interval_seconds=0.05,  # 50 ms
        )

        # Hammer the callback 50 times in quick succession (no awaits between).
        for i in range(1, 51):
            await emitter(i, 1000, f"{i}/1000")

        # Wait for tail-flush timer to fire.
        await asyncio.sleep(0.1)

        # First call emits immediately. Some number of subsequent calls may
        # cross window boundaries and emit (depends on scheduling). Tail
        # flush emits the final suppressed (50, 1000, ...) tuple. Bound the
        # total emits to a sane ceiling that proves throttling worked.
        assert mock_manager.emit_progress.await_count >= 2
        assert mock_manager.emit_progress.await_count <= 10

        # The last emission must reflect the most recent call.
        last_event = mock_manager.emit_progress.call_args[0][0]
        assert last_event.current == 50
        assert last_event.total == 1000

    async def test_aclose_cancels_pending_tail(self):
        """No stale progress fires after the emitter is closed."""
        mock_manager = _make_mock_manager()

        emitter = await create_throttled_sub_operation(
            progress_manager=mock_manager,
            description="x",
            total_items=100,
            parent_operation_id="p",
            phase="enrich",
            node_type="enricher",
            min_interval_seconds=0.5,  # 500 ms — tail won't fire before our wait
        )

        # First call emits immediately.
        await emitter(1, 100, "1/100")
        # Second call within window — schedules tail.
        await emitter(2, 100, "2/100")
        # Tail is pending; cancel via aclose().
        await emitter.aclose()
        # Wait longer than min_interval to ensure the tail would have fired.
        await asyncio.sleep(0.6)

        # Only the first call's emit should have happened.
        assert mock_manager.emit_progress.await_count == 1
        # complete_operation called once on the manager
        mock_manager.complete_operation.assert_awaited_once_with(
            emitter.sub_op_id, OperationStatus.COMPLETED
        )


class TestEmitPhaseProgress:
    """Tests the lightweight phase-transition helper."""

    async def test_emit_phase_progress_creates_and_completes(self):
        mock_manager = _make_mock_manager()

        await emit_phase_progress(
            progress_manager=mock_manager,
            parent_operation_id="parent-1",
            phase="fetch",
            node_type="source",
            message="Fetching playlist from Spotify",
        )

        # Should have called start_operation once
        mock_manager.start_operation.assert_awaited_once()
        created_op = mock_manager.start_operation.call_args[0][0]
        assert created_op.description == "Fetching playlist from Spotify"
        assert created_op.total_items is None  # indeterminate
        assert created_op.metadata["parent_operation_id"] == "parent-1"
        assert created_op.metadata["phase"] == "fetch"
        assert created_op.metadata["node_type"] == "source"

        # Should have emitted one progress event
        mock_manager.emit_progress.assert_awaited_once()
        emitted_event = mock_manager.emit_progress.call_args[0][0]
        assert emitted_event.operation_id == "sub-op-123"
        assert emitted_event.current == 0
        assert emitted_event.total is None
        assert emitted_event.message == "Fetching playlist from Spotify"

        # Should have completed immediately
        mock_manager.complete_operation.assert_awaited_once_with(
            "sub-op-123", OperationStatus.COMPLETED
        )
