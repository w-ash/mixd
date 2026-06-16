"""Integration tests for the complete progress tracking system.

Tests the full chain from domain entities through application services to
interface providers, ensuring all components work together correctly.
"""

import asyncio
from asyncio import CancelledError
from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.application.services.progress_broker import ProgressBroker
from src.domain.entities.progress import (
    OperationStatus,
    ProgressStatus,
    create_progress_event,
    create_progress_operation,
)
from src.interface.cli.progress_subscriber import RichProgressSubscriber


class TestProgressIntegration:
    """Integration tests for complete progress tracking system."""

    @pytest.fixture
    def progress_broker(self):
        """Create progress manager for tests."""
        return ProgressBroker()

    @pytest.fixture
    def rich_provider(self):
        """Create Rich progress provider with default console."""
        # Use default console for tests - Rich will handle terminal detection
        return RichProgressSubscriber()

    async def test_complete_progress_flow(self, progress_broker, rich_provider):
        """Test complete progress flow from operation start to completion."""
        # Subscribe Rich provider to progress manager
        subscription_id = await progress_broker.subscribe(rich_provider)

        # Create and start operation
        operation = create_progress_operation(
            description="Test import operation", total_items=100
        )

        operation_id = await progress_broker.start_operation(operation)
        assert operation_id == operation.operation_id

        # Send progress events
        events = [
            create_progress_event(operation_id, 0, 100, "Starting..."),
            create_progress_event(operation_id, 25, 100, "Processing batch 1"),
            create_progress_event(operation_id, 50, 100, "Processing batch 2"),
            create_progress_event(operation_id, 75, 100, "Processing batch 3"),
            create_progress_event(
                operation_id, 100, 100, "Finalizing...", ProgressStatus.COMPLETED
            ),
        ]

        for event in events:
            await progress_broker.emit_progress(event)
            await asyncio.sleep(0.01)  # Small delay to avoid rate limiting

        # Complete the operation
        await progress_broker.complete_operation(
            operation_id, OperationStatus.COMPLETED
        )

        # Verify operation is completed — completing again must fail
        with pytest.raises(ValueError, match="No operation found"):
            await progress_broker.complete_operation(
                operation_id, OperationStatus.COMPLETED
            )

        # Cleanup subscription
        unsubscribed = await progress_broker.unsubscribe(subscription_id)
        assert unsubscribed is True

    async def test_multiple_concurrent_operations(self, progress_broker, rich_provider):
        """Test handling multiple concurrent operations."""
        # Subscribe provider
        await progress_broker.subscribe(rich_provider)

        # Create multiple operations
        operations = [
            create_progress_operation(f"Operation {i}", total_items=50)
            for i in range(3)
        ]

        # Start all operations
        operation_ids = []
        for operation in operations:
            op_id = await progress_broker.start_operation(operation)
            operation_ids.append(op_id)

        # Verify all are active — restarting a tracked operation must fail
        for operation in operations:
            with pytest.raises(ValueError, match="already being tracked"):
                await progress_broker.start_operation(operation)

        # Send progress for all operations
        for i, op_id in enumerate(operation_ids):
            for current in [10, 25, 40, 50]:
                event = create_progress_event(
                    op_id, current, 50, f"Operation {i} at {current}/50"
                )
                await progress_broker.emit_progress(event)
                await asyncio.sleep(0.01)  # Small delay to avoid rate limiting

        # Complete all operations
        for op_id in operation_ids:
            await progress_broker.complete_operation(op_id, OperationStatus.COMPLETED)

        # Verify none remain active — completing again must fail
        for op_id in operation_ids:
            with pytest.raises(ValueError, match="No operation found"):
                await progress_broker.complete_operation(
                    op_id, OperationStatus.COMPLETED
                )

    async def test_indeterminate_progress(self, progress_broker, rich_provider):
        """Test progress tracking for indeterminate operations."""
        await progress_broker.subscribe(rich_provider)

        # Create indeterminate operation (no total_items)
        operation = create_progress_operation(
            description="Scanning files...",
            total_items=None,  # Indeterminate
        )

        operation_id = await progress_broker.start_operation(operation)

        # Send progress events without total
        events = [
            create_progress_event(operation_id, 150, None, "Found 150 files..."),
            create_progress_event(operation_id, 327, None, "Found 327 files..."),
            create_progress_event(
                operation_id, 500, None, "Scan complete - found 500 files"
            ),
        ]

        for event in events:
            await progress_broker.emit_progress(event)
            await asyncio.sleep(0.01)  # Small delay to avoid rate limiting

        await progress_broker.complete_operation(
            operation_id, OperationStatus.COMPLETED
        )

        # Verify operation completed successfully — completing again must fail
        with pytest.raises(ValueError, match="No operation found"):
            await progress_broker.complete_operation(
                operation_id, OperationStatus.COMPLETED
            )

    async def test_operation_failure_handling(self, progress_broker, rich_provider):
        """Test handling of failed operations."""
        await progress_broker.subscribe(rich_provider)

        operation = create_progress_operation(
            description="Risky operation", total_items=10
        )

        operation_id = await progress_broker.start_operation(operation)

        # Make some progress
        await progress_broker.emit_progress(
            create_progress_event(operation_id, 5, 10, "Processing...")
        )

        # Fail the operation
        await progress_broker.complete_operation(operation_id, OperationStatus.FAILED)

        # Verify operation is finalized — completing again must fail
        with pytest.raises(ValueError, match="No operation found"):
            await progress_broker.complete_operation(
                operation_id, OperationStatus.FAILED
            )

    async def test_invalid_progress_is_dropped_not_raised(self, progress_broker):
        """Invalid progress is observational telemetry — it is logged and dropped,
        never raised. A monotonicity violation (e.g. a coarse pipeline meter
        overlapping a fine sub-meter) must NOT abort the operation it tracks; the
        re-raise that did exactly that silently failed web imports (v0.8.5)."""
        operation = create_progress_operation(
            description="Validation test", total_items=100
        )

        operation_id = await progress_broker.start_operation(operation)

        # Valid progress event should work
        await progress_broker.emit_progress(
            create_progress_event(operation_id, 25, 100, "Valid progress")
        )

        # Backwards progress must NOT raise — it is dropped (the operation's
        # recorded position stays at 25).
        await progress_broker.emit_progress(
            create_progress_event(operation_id, 15, 100, "Backwards progress")
        )

        # And the operation keeps accepting valid progress afterward.
        await progress_broker.emit_progress(
            create_progress_event(operation_id, 30, 100, "Still running")
        )

    async def test_subscriber_error_isolation(self, progress_broker):
        """Test that subscriber errors don't crash the progress system."""
        # Create a subscriber that always fails
        failing_subscriber = Mock()
        failing_subscriber.on_progress_event.side_effect = Exception("Subscriber error")
        failing_subscriber.on_operation_started.side_effect = Exception(
            "Subscriber error"
        )
        failing_subscriber.on_operation_completed.side_effect = Exception(
            "Subscriber error"
        )

        # Subscribe the failing subscriber
        await progress_broker.subscribe(failing_subscriber)

        # Operations should still work despite subscriber failures
        operation = create_progress_operation(
            description="Test with failing subscriber"
        )
        operation_id = await progress_broker.start_operation(operation)

        # These should not raise exceptions despite subscriber failures
        await progress_broker.emit_progress(
            create_progress_event(operation_id, 50, 100, "Progress")
        )
        await progress_broker.complete_operation(
            operation_id, OperationStatus.COMPLETED
        )

        # Operation should complete successfully — completing again must fail
        with pytest.raises(ValueError, match="No operation found"):
            await progress_broker.complete_operation(
                operation_id, OperationStatus.COMPLETED
            )

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
        """External CancelledError at `await gather()` must NOT kill the workflow.

        Prefect cancel scopes inject CancelledError at the gather() call site
        (parent level), not inside child coroutines. return_exceptions=True
        only captures child exceptions, so the external CancelledError would
        propagate and kill the workflow. _broadcast catches it and calls
        task.uncancel() to clear the pending cancel request.
        """
        subscriber = AsyncMock()
        await progress_broker.subscribe(subscriber)

        operation = create_progress_operation(description="Test external cancellation")
        operation_id = await progress_broker.start_operation(operation)

        # Patch asyncio.gather to raise CancelledError (simulating Prefect
        # cancel scope injected at the await point). The generator expression
        # materializes child coroutines before gather runs, so close them
        # ourselves to avoid orphan-coroutine warnings.
        def cancel_gather(*coros, return_exceptions=False):
            for coro in coros:
                if hasattr(coro, "close"):
                    coro.close()
            raise CancelledError()

        with patch(
            "src.application.services.progress_broker.asyncio.gather",
            side_effect=cancel_gather,
        ):
            # Should NOT raise — _broadcast absorbs the CancelledError
            await progress_broker.emit_progress(
                create_progress_event(operation_id, 50, 100, "Progress")
            )


class TestProgressSystemExample:
    """Example usage of the complete progress system."""

    async def test_realistic_batch_processing_example(self):
        """Example simulating realistic batch processing with progress tracking."""
        # Setup progress system
        progress_broker = ProgressBroker()

        # Create Rich provider (in real usage, this would display to terminal)
        rich_provider = RichProgressSubscriber()

        # Subscribe provider to manager
        subscriber_id = await progress_broker.subscribe(rich_provider)

        async with rich_provider:  # Start progress display
            # Simulate batch processing operation
            operation = create_progress_operation(
                description="Importing tracks from Last.fm",
                total_items=1000,
                source="lastfm",
                batch_size=50,
            )

            operation_id = await progress_broker.start_operation(operation)

            # Simulate batch processing with progress updates
            batch_size = 50
            total_items = 1000

            for batch_start in range(0, total_items, batch_size):
                batch_end = min(batch_start + batch_size, total_items)

                # Emit progress event for this batch
                await progress_broker.emit_progress(
                    create_progress_event(
                        operation_id=operation_id,
                        current=batch_end,
                        total=total_items,
                        message=f"Processed batch {batch_start // batch_size + 1}/{total_items // batch_size}",
                        batch_start=batch_start,
                        batch_size=batch_end - batch_start,
                    )
                )

                # Simulate processing time
                await asyncio.sleep(0.01)  # Small delay to simulate work

            # Complete the operation
            await progress_broker.complete_operation(
                operation_id, OperationStatus.COMPLETED
            )

            # Verify final state — completing again must fail
            with pytest.raises(ValueError, match="No operation found"):
                await progress_broker.complete_operation(
                    operation_id, OperationStatus.COMPLETED
                )

        # Cleanup
        await progress_broker.unsubscribe(subscriber_id)

        # Verify system cleaned up properly
        assert len(progress_broker._subscribers) == 0
