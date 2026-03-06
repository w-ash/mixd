"""Unit tests for the sub-operation progress bridge.

Tests create_sub_operation, complete_sub_operation, and emit_phase_progress
functions that bridge infrastructure callbacks to AsyncProgressManager.
"""

from unittest.mock import AsyncMock, call

from src.application.services.sub_operation_progress import (
    complete_sub_operation,
    create_sub_operation,
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

        await complete_sub_operation(
            mock_manager, "sub-op-456", OperationStatus.FAILED
        )

        mock_manager.complete_operation.assert_awaited_once_with(
            "sub-op-456", OperationStatus.FAILED
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
