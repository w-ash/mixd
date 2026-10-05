"""Integration tests for ProgressBroker fan-out.

Drives the broker with the real OperationLedger and asserts what subscribers
receive: lifecycle notifications in order, dropped invalid events, and
isolation from failing or cancelled subscribers.
"""

import asyncio
from asyncio import CancelledError
from unittest.mock import AsyncMock, Mock

import pytest

from src.application.services.progress_broker import ProgressBroker
from src.domain.entities.progress import (
    OperationStatus,
    ProgressEvent,
    ProgressOperation,
    ProgressStatus,
    create_progress_event,
    create_progress_operation,
)


class _RecordingSubscriber:
    """Record every notification the broker delivers, in order."""

    def __init__(self) -> None:
        self.started: list[str] = []
        self.events: list[tuple[str, int, int | None]] = []
        self.completed: list[tuple[str, OperationStatus]] = []

    async def on_operation_started(self, operation: ProgressOperation) -> None:
        self.started.append(operation.operation_id)

    async def on_progress_event(self, event: ProgressEvent) -> None:
        self.events.append((event.operation_id, event.current, event.total))

    async def on_operation_completed(
        self, operation_id: str, final_status: OperationStatus
    ) -> None:
        self.completed.append((operation_id, final_status))


class TestProgressIntegration:
    """Broker lifecycle as seen by subscribers."""

    @pytest.fixture
    def progress_broker(self):
        return ProgressBroker()

    @pytest.fixture
    def recorder(self):
        return _RecordingSubscriber()

    async def test_complete_progress_flow(self, progress_broker, recorder):
        """Start, progress, and completion reach the subscriber in order;
        after unsubscribing, nothing more arrives."""
        subscription_id = await progress_broker.subscribe(recorder)
        operation = create_progress_operation(
            description="Test import operation", total_items=100
        )

        operation_id = await progress_broker.start_operation(operation)
        for current in (0, 25, 50, 75):
            await progress_broker.emit_progress(
                create_progress_event(operation_id, current, 100, "Processing")
            )
        await progress_broker.emit_progress(
            create_progress_event(
                operation_id, 100, 100, "Finalizing", ProgressStatus.COMPLETED
            )
        )
        await progress_broker.complete_operation(
            operation_id, OperationStatus.COMPLETED
        )

        assert operation_id == operation.operation_id
        assert recorder.started == [operation_id]
        assert recorder.events == [
            (operation_id, 0, 100),
            (operation_id, 25, 100),
            (operation_id, 50, 100),
            (operation_id, 75, 100),
            (operation_id, 100, 100),
        ]
        assert recorder.completed == [(operation_id, OperationStatus.COMPLETED)]

        # A finished operation is no longer tracked.
        with pytest.raises(ValueError, match="No operation found"):
            await progress_broker.complete_operation(
                operation_id, OperationStatus.COMPLETED
            )

        assert await progress_broker.unsubscribe(subscription_id) is True
        later = await progress_broker.start_operation(
            create_progress_operation("After unsubscribe", total_items=1)
        )
        assert later not in recorder.started

    async def test_multiple_concurrent_operations(self, progress_broker, recorder):
        """Several live operations are tracked independently; restarting one
        that is still tracked is rejected."""
        await progress_broker.subscribe(recorder)
        operations = [
            create_progress_operation(f"Operation {i}", total_items=50)
            for i in range(3)
        ]
        operation_ids = [
            await progress_broker.start_operation(operation) for operation in operations
        ]

        for operation in operations:
            with pytest.raises(ValueError, match="already being tracked"):
                await progress_broker.start_operation(operation)

        # Interleave the operations' events.
        for current in (10, 25, 50):
            for op_id in operation_ids:
                await progress_broker.emit_progress(
                    create_progress_event(op_id, current, 50, "step")
                )
        for op_id in operation_ids:
            await progress_broker.complete_operation(op_id, OperationStatus.COMPLETED)

        assert recorder.started == operation_ids
        for op_id in operation_ids:
            assert [c for o, c, _ in recorder.events if o == op_id] == [10, 25, 50]
        assert recorder.completed == [
            (op_id, OperationStatus.COMPLETED) for op_id in operation_ids
        ]

    async def test_indeterminate_progress(self, progress_broker, recorder):
        """An operation with no known total still delivers its counts."""
        await progress_broker.subscribe(recorder)
        operation_id = await progress_broker.start_operation(
            create_progress_operation(description="Scanning files", total_items=None)
        )

        for current in (150, 327, 500):
            await progress_broker.emit_progress(
                create_progress_event(operation_id, current, None, "Scanning")
            )
        await progress_broker.complete_operation(
            operation_id, OperationStatus.COMPLETED
        )

        assert recorder.events == [
            (operation_id, 150, None),
            (operation_id, 327, None),
            (operation_id, 500, None),
        ]
        assert recorder.completed == [(operation_id, OperationStatus.COMPLETED)]

    async def test_operation_failure_handling(self, progress_broker, recorder):
        """A failed operation reaches subscribers as FAILED, not COMPLETED."""
        await progress_broker.subscribe(recorder)
        operation_id = await progress_broker.start_operation(
            create_progress_operation(description="Risky operation", total_items=10)
        )
        await progress_broker.emit_progress(
            create_progress_event(operation_id, 5, 10, "Processing")
        )

        await progress_broker.complete_operation(operation_id, OperationStatus.FAILED)

        assert recorder.completed == [(operation_id, OperationStatus.FAILED)]
        with pytest.raises(ValueError, match="No operation found"):
            await progress_broker.complete_operation(
                operation_id, OperationStatus.FAILED
            )

    async def test_invalid_progress_is_dropped_not_raised(
        self, progress_broker, recorder
    ):
        """Invalid progress is observational telemetry — it is logged and dropped,
        never raised. A monotonicity violation (e.g. a coarse pipeline meter
        overlapping a fine sub-meter) must NOT abort the operation it tracks; the
        re-raise that did exactly that silently failed web imports (v0.8.5)."""
        await progress_broker.subscribe(recorder)
        operation_id = await progress_broker.start_operation(
            create_progress_operation(description="Validation test", total_items=100)
        )

        await progress_broker.emit_progress(
            create_progress_event(operation_id, 25, 100, "Valid progress")
        )
        await progress_broker.emit_progress(
            create_progress_event(operation_id, 15, 100, "Backwards progress")
        )
        await progress_broker.emit_progress(
            create_progress_event(operation_id, 30, 100, "Still running")
        )

        # The backwards event never reaches subscribers; the operation goes on.
        assert recorder.events == [(operation_id, 25, 100), (operation_id, 30, 100)]

    async def test_subscriber_error_isolation(self, progress_broker, recorder):
        """A subscriber that raises on every call neither breaks the operation
        nor starves the other subscribers."""
        failing_subscriber = Mock()
        failing_subscriber.on_progress_event.side_effect = Exception("Subscriber error")
        failing_subscriber.on_operation_started.side_effect = Exception(
            "Subscriber error"
        )
        failing_subscriber.on_operation_completed.side_effect = Exception(
            "Subscriber error"
        )
        await progress_broker.subscribe(failing_subscriber)
        await progress_broker.subscribe(recorder)

        operation_id = await progress_broker.start_operation(
            create_progress_operation(description="Test with failing subscriber")
        )
        await progress_broker.emit_progress(
            create_progress_event(operation_id, 50, 100, "Progress")
        )
        await progress_broker.complete_operation(
            operation_id, OperationStatus.COMPLETED
        )

        assert recorder.started == [operation_id]
        assert recorder.events == [(operation_id, 50, 100)]
        assert recorder.completed == [(operation_id, OperationStatus.COMPLETED)]

    async def test_subscriber_cancelled_error_does_not_propagate(self, progress_broker):
        """CancelledError from a subscriber must not crash the publishing operation.

        Regression: TaskGroup propagates BaseException (including CancelledError
        injected by Prefect's cancel scope), violating subscriber isolation.
        With gather(return_exceptions=True) the error is captured, not propagated.
        """
        # Create a subscriber that raises CancelledError (simulates Prefect timeout)
        cancelling_subscriber = AsyncMock()
        cancelling_subscriber.on_progress_event.side_effect = CancelledError()
        cancelling_subscriber.on_operation_started = AsyncMock()
        cancelling_subscriber.on_operation_completed = AsyncMock()

        await progress_broker.subscribe(cancelling_subscriber)

        # Operation lifecycle should succeed despite CancelledError in subscriber
        operation = create_progress_operation(
            description="Test with cancelling subscriber"
        )
        operation_id = await progress_broker.start_operation(operation)

        # This must NOT raise CancelledError
        await progress_broker.emit_progress(
            create_progress_event(operation_id, 50, 100, "Progress")
        )
        await progress_broker.complete_operation(
            operation_id, OperationStatus.COMPLETED
        )

        # Completion was broadcast to the (cancelling) subscriber
        cancelling_subscriber.on_operation_completed.assert_awaited_once_with(
            operation_id, OperationStatus.COMPLETED
        )

    async def test_external_cancellation_of_gather_is_absorbed(self, progress_broker):
        """External cancellation at `await gather()` must NOT kill the workflow.

        A cancel scope or server reload cancels the publishing task while it
        awaits subscriber notification. ``_broadcast`` absorbs the
        CancelledError and calls ``task.uncancel()`` so the request does not
        re-fire at the caller's next await.
        """
        entered = asyncio.Event()

        class _BlockingSubscriber(_RecordingSubscriber):
            async def on_progress_event(self, event: ProgressEvent) -> None:
                entered.set()
                await asyncio.Event().wait()

        await progress_broker.subscribe(_BlockingSubscriber())
        operation_id = await progress_broker.start_operation(
            create_progress_operation(description="Test external cancellation")
        )

        async def publish() -> str:
            await progress_broker.emit_progress(
                create_progress_event(operation_id, 50, 100, "Progress")
            )
            return "survived"

        publisher = asyncio.create_task(publish())
        async with asyncio.timeout(5):
            await entered.wait()
        publisher.cancel()

        assert await publisher == "survived"
        assert publisher.cancelling() == 0
