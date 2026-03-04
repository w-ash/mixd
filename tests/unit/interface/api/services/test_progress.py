"""Unit tests for SSE progress infrastructure.

Tests OperationBoundEmitter, SSEOperationRegistry, and SSEProgressSubscriber
using mock progress managers and in-memory queues.
"""

import asyncio
from unittest.mock import AsyncMock

from src.domain.entities.progress import (
    OperationStatus,
    ProgressOperation,
    create_progress_event,
    create_progress_operation,
)
from src.interface.api.services.progress import (
    SSE_SENTINEL,
    OperationBoundEmitter,
    SSEOperationRegistry,
    SSEProgressSubscriber,
)

# ---------------------------------------------------------------------------
# OperationBoundEmitter
# ---------------------------------------------------------------------------


class TestOperationBoundEmitter:
    """Tests that the emitter wrapper correctly substitutes operation IDs."""

    async def test_start_operation_substitutes_id(self):
        delegate = AsyncMock()
        delegate.start_operation = AsyncMock(return_value="pre-generated-id")
        emitter = OperationBoundEmitter(delegate, operation_id="pre-generated-id")

        operation = create_progress_operation("Test operation")
        result = await emitter.start_operation(operation)

        assert result == "pre-generated-id"
        # The delegate received an operation with the pre-generated ID
        call_args = delegate.start_operation.call_args[0][0]
        assert call_args.operation_id == "pre-generated-id"
        assert call_args.description == "Test operation"

    async def test_emit_progress_passes_through(self):
        delegate = AsyncMock()
        emitter = OperationBoundEmitter(delegate, operation_id="op-123")

        event = create_progress_event("op-123", current=5, total=10, message="Working")
        await emitter.emit_progress(event)

        delegate.emit_progress.assert_awaited_once_with(event)

    async def test_complete_operation_passes_through(self):
        delegate = AsyncMock()
        emitter = OperationBoundEmitter(delegate, operation_id="op-123")

        await emitter.complete_operation("op-123", OperationStatus.COMPLETED)

        delegate.complete_operation.assert_awaited_once_with(
            "op-123", OperationStatus.COMPLETED
        )


# ---------------------------------------------------------------------------
# SSEOperationRegistry
# ---------------------------------------------------------------------------


class TestSSEOperationRegistry:
    """Tests registry CRUD operations for operation queues."""

    async def test_register_returns_queue(self):
        registry = SSEOperationRegistry()
        queue = await registry.register("op-1")

        assert isinstance(queue, asyncio.Queue)

    async def test_get_queue_returns_registered_queue(self):
        registry = SSEOperationRegistry()
        original = await registry.register("op-1")
        retrieved = await registry.get_queue("op-1")

        assert retrieved is original

    async def test_get_queue_returns_none_for_unknown(self):
        registry = SSEOperationRegistry()
        result = await registry.get_queue("nonexistent")

        assert result is None

    async def test_unregister_removes_queue(self):
        registry = SSEOperationRegistry()
        await registry.register("op-1")
        await registry.unregister("op-1")

        assert await registry.get_queue("op-1") is None

    async def test_unregister_unknown_is_noop(self):
        registry = SSEOperationRegistry()
        await registry.unregister("nonexistent")  # Should not raise

    async def test_get_active_operation_ids(self):
        registry = SSEOperationRegistry()
        await registry.register("op-1")
        await registry.register("op-2")

        ids = await registry.get_active_operation_ids()
        assert sorted(ids) == ["op-1", "op-2"]

    async def test_get_active_operation_ids_empty(self):
        registry = SSEOperationRegistry()
        ids = await registry.get_active_operation_ids()

        assert ids == []


# ---------------------------------------------------------------------------
# SSEProgressSubscriber
# ---------------------------------------------------------------------------


class TestSSEProgressSubscriber:
    """Tests that the subscriber correctly routes events to SSE queues."""

    async def test_on_operation_started_puts_event(self):
        registry = SSEOperationRegistry()
        queue = await registry.register("op-1")
        subscriber = SSEProgressSubscriber(registry)

        operation = ProgressOperation(
            operation_id="op-1",
            description="Test import",
            total_items=100,
            status=OperationStatus.RUNNING,
        )
        await subscriber.on_operation_started(operation)

        event = queue.get_nowait()
        assert event["event"] == "started"
        assert event["id"] == "evt_1"
        assert event["data"]["operation_id"] == "op-1"
        assert event["data"]["description"] == "Test import"
        assert event["data"]["total"] == 100

    async def test_on_progress_event_puts_event(self):
        registry = SSEOperationRegistry()
        queue = await registry.register("op-1")
        subscriber = SSEProgressSubscriber(registry)

        # Initialize counter
        operation = ProgressOperation(
            operation_id="op-1", status=OperationStatus.RUNNING
        )
        await subscriber.on_operation_started(operation)
        queue.get_nowait()  # Drain the started event

        event = create_progress_event(
            "op-1", current=50, total=100, message="Halfway"
        )
        await subscriber.on_progress_event(event)

        sse_event = queue.get_nowait()
        assert sse_event["event"] == "progress"
        assert sse_event["id"] == "evt_2"
        assert sse_event["data"]["current"] == 50
        assert sse_event["data"]["total"] == 100
        assert sse_event["data"]["message"] == "Halfway"
        assert sse_event["data"]["completion_percentage"] == 50.0

    async def test_on_operation_completed_puts_complete_and_sentinel(self):
        registry = SSEOperationRegistry()
        queue = await registry.register("op-1")
        subscriber = SSEProgressSubscriber(registry)

        await subscriber.on_operation_completed("op-1", OperationStatus.COMPLETED)

        complete_event = queue.get_nowait()
        assert complete_event["event"] == "complete"
        assert complete_event["data"]["final_status"] == "completed"

        sentinel = queue.get_nowait()
        assert sentinel is SSE_SENTINEL

    async def test_on_operation_failed_puts_error_event(self):
        registry = SSEOperationRegistry()
        queue = await registry.register("op-1")
        subscriber = SSEProgressSubscriber(registry)

        await subscriber.on_operation_completed("op-1", OperationStatus.FAILED)

        error_event = queue.get_nowait()
        assert error_event["event"] == "error"
        assert error_event["data"]["final_status"] == "failed"

    async def test_ignores_unregistered_operations(self):
        registry = SSEOperationRegistry()
        subscriber = SSEProgressSubscriber(registry)

        # These should not raise even though "unknown" isn't registered
        operation = ProgressOperation(
            operation_id="unknown", status=OperationStatus.RUNNING
        )
        await subscriber.on_operation_started(operation)

        event = create_progress_event("unknown", current=1, total=10, message="Test")
        await subscriber.on_progress_event(event)

        await subscriber.on_operation_completed("unknown", OperationStatus.COMPLETED)

    async def test_event_counter_increments(self):
        registry = SSEOperationRegistry()
        queue = await registry.register("op-1")
        subscriber = SSEProgressSubscriber(registry)

        operation = ProgressOperation(
            operation_id="op-1", status=OperationStatus.RUNNING
        )
        await subscriber.on_operation_started(operation)

        for i in range(3):
            event = create_progress_event(
                "op-1", current=i, total=10, message=f"Step {i}"
            )
            await subscriber.on_progress_event(event)

        # Drain all events and check IDs
        events = []
        while not queue.empty():
            events.append(queue.get_nowait())

        ids = [e["id"] for e in events]
        assert ids == ["evt_1", "evt_2", "evt_3", "evt_4"]  # started + 3 progress
