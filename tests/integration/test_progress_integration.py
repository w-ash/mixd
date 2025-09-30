"""Integration tests for the complete progress tracking system.

Tests the full chain from domain entities through application services to
interface providers, ensuring all components work together correctly.
"""

import asyncio
from unittest.mock import Mock

import pytest

from src.application.services.progress_manager import AsyncProgressManager
from src.domain.entities.progress import (
    OperationStatus,
    ProgressStatus,
    create_progress_event,
    create_progress_operation,
)
from src.interface.cli.progress_provider import RichProgressProvider


class TestProgressIntegration:
    """Integration tests for complete progress tracking system."""

    @pytest.fixture
    def progress_manager(self):
        """Create progress manager for tests."""
        return AsyncProgressManager()

    @pytest.fixture
    def rich_provider(self):
        """Create Rich progress provider with default console."""
        # Use default console for tests - Rich will handle terminal detection
        return RichProgressProvider()

    @pytest.mark.asyncio
    async def test_complete_progress_flow(self, progress_manager, rich_provider):
        """Test complete progress flow from operation start to completion."""
        # Subscribe Rich provider to progress manager
        subscription_id = await progress_manager.subscribe(rich_provider)

        # Create and start operation
        operation = create_progress_operation(
            description="Test import operation", total_items=100
        )

        operation_id = await progress_manager.start_operation(operation)
        assert operation_id == operation.operation_id

        # Verify operation was started
        retrieved_operation = await progress_manager.get_operation(operation_id)
        assert retrieved_operation is not None
        assert retrieved_operation.status == OperationStatus.RUNNING

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
            await progress_manager.emit_progress(event)
            await asyncio.sleep(0.01)  # Small delay to avoid rate limiting

        # Complete the operation
        await progress_manager.complete_operation(
            operation_id, OperationStatus.COMPLETED
        )

        # Verify operation is completed
        final_operation = await progress_manager.get_operation(operation_id)
        assert final_operation.status == OperationStatus.COMPLETED
        assert final_operation.duration_seconds is not None
        assert final_operation.duration_seconds >= 0

        # Cleanup subscription
        unsubscribed = await progress_manager.unsubscribe(subscription_id)
        assert unsubscribed is True

    @pytest.mark.asyncio
    async def test_multiple_concurrent_operations(
        self, progress_manager, rich_provider
    ):
        """Test handling multiple concurrent operations."""
        # Subscribe provider
        await progress_manager.subscribe(rich_provider)

        # Create multiple operations
        operations = [
            create_progress_operation(f"Operation {i}", total_items=50)
            for i in range(3)
        ]

        # Start all operations
        operation_ids = []
        for operation in operations:
            op_id = await progress_manager.start_operation(operation)
            operation_ids.append(op_id)

        # Verify all are active
        active_operations = await progress_manager.get_active_operations()
        assert len(active_operations) == 3

        # Send progress for all operations
        for i, op_id in enumerate(operation_ids):
            for current in [10, 25, 40, 50]:
                event = create_progress_event(
                    op_id, current, 50, f"Operation {i} at {current}/50"
                )
                await progress_manager.emit_progress(event)
                await asyncio.sleep(0.01)  # Small delay to avoid rate limiting

        # Complete all operations
        for op_id in operation_ids:
            await progress_manager.complete_operation(op_id, OperationStatus.COMPLETED)

        # Verify no active operations remain
        active_operations = await progress_manager.get_active_operations()
        assert len(active_operations) == 0

    @pytest.mark.asyncio
    async def test_indeterminate_progress(self, progress_manager, rich_provider):
        """Test progress tracking for indeterminate operations."""
        await progress_manager.subscribe(rich_provider)

        # Create indeterminate operation (no total_items)
        operation = create_progress_operation(
            description="Scanning files...",
            total_items=None,  # Indeterminate
        )

        operation_id = await progress_manager.start_operation(operation)

        # Send progress events without total
        events = [
            create_progress_event(operation_id, 150, None, "Found 150 files..."),
            create_progress_event(operation_id, 327, None, "Found 327 files..."),
            create_progress_event(
                operation_id, 500, None, "Scan complete - found 500 files"
            ),
        ]

        for event in events:
            await progress_manager.emit_progress(event)
            await asyncio.sleep(0.01)  # Small delay to avoid rate limiting

        await progress_manager.complete_operation(
            operation_id, OperationStatus.COMPLETED
        )

        # Verify operation completed successfully
        final_operation = await progress_manager.get_operation(operation_id)
        assert final_operation.status == OperationStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_operation_failure_handling(self, progress_manager, rich_provider):
        """Test handling of failed operations."""
        await progress_manager.subscribe(rich_provider)

        operation = create_progress_operation(
            description="Risky operation", total_items=10
        )

        operation_id = await progress_manager.start_operation(operation)

        # Make some progress
        await progress_manager.emit_progress(
            create_progress_event(operation_id, 5, 10, "Processing...")
        )

        # Fail the operation
        await progress_manager.complete_operation(operation_id, OperationStatus.FAILED)

        # Verify operation failed
        final_operation = await progress_manager.get_operation(operation_id)
        assert final_operation.status == OperationStatus.FAILED

    @pytest.mark.asyncio
    async def test_progress_validation_enforcement(self, progress_manager):
        """Test that domain validation rules are enforced."""
        operation = create_progress_operation(
            description="Validation test", total_items=100
        )

        operation_id = await progress_manager.start_operation(operation)

        # Valid progress event should work
        valid_event = create_progress_event(operation_id, 25, 100, "Valid progress")
        await progress_manager.emit_progress(valid_event)

        # Invalid progress event (backwards progress) should fail
        invalid_event = create_progress_event(
            operation_id, 15, 100, "Backwards progress"
        )

        with pytest.raises(ValueError, match="Progress went backwards"):
            await progress_manager.emit_progress(invalid_event)

        # Operation should still be running after validation failure
        operation_state = await progress_manager.get_operation(operation_id)
        assert operation_state.status == OperationStatus.RUNNING

    @pytest.mark.asyncio
    async def test_subscriber_error_isolation(self, progress_manager):
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
        await progress_manager.subscribe(failing_subscriber)

        # Operations should still work despite subscriber failures
        operation = create_progress_operation(
            description="Test with failing subscriber"
        )
        operation_id = await progress_manager.start_operation(operation)

        # These should not raise exceptions despite subscriber failures
        await progress_manager.emit_progress(
            create_progress_event(operation_id, 50, 100, "Progress")
        )
        await progress_manager.complete_operation(
            operation_id, OperationStatus.COMPLETED
        )

        # Operation should complete successfully
        final_operation = await progress_manager.get_operation(operation_id)
        assert final_operation.status == OperationStatus.COMPLETED


class TestRichProgressProvider:
    """Integration tests specifically for Rich progress provider."""

    @pytest.fixture
    def rich_provider(self):
        """Create Rich provider with default console."""
        return RichProgressProvider()

    @pytest.mark.asyncio
    async def test_rich_provider_lifecycle(self, rich_provider):
        """Test Rich provider start/stop lifecycle."""
        assert not rich_provider.is_display_active

        await rich_provider.start_display()
        assert rich_provider.is_display_active

        await rich_provider.stop_display()
        assert not rich_provider.is_display_active

    @pytest.mark.asyncio
    async def test_rich_provider_context_manager(self, rich_provider):
        """Test Rich provider as async context manager."""
        assert not rich_provider.is_display_active

        async with rich_provider:
            assert rich_provider.is_display_active

        assert not rich_provider.is_display_active

    @pytest.mark.asyncio
    async def test_rich_provider_operation_tracking(self, rich_provider):
        """Test Rich provider tracks operations correctly."""
        # Start display
        await rich_provider.start_display()

        # Create test operation
        operation = create_progress_operation(
            description="Test tracking", total_items=10
        )

        # Start operation
        await rich_provider.on_operation_started(operation)
        assert rich_provider.active_operation_count == 1

        # Send progress events
        await rich_provider.on_progress_event(
            create_progress_event(operation.operation_id, 5, 10, "Half done")
        )

        # Complete operation
        await rich_provider.on_operation_completed(
            operation.operation_id, OperationStatus.COMPLETED
        )

        # Operation should be marked inactive immediately
        # (cleanup happens after delay)
        assert rich_provider.active_operation_count == 0

        await rich_provider.stop_display()


class TestProgressSystemExample:
    """Example usage of the complete progress system."""

    @pytest.mark.asyncio
    async def test_realistic_batch_processing_example(self):
        """Example simulating realistic batch processing with progress tracking."""
        # Setup progress system
        progress_manager = AsyncProgressManager()

        # Create Rich provider (in real usage, this would display to terminal)
        rich_provider = RichProgressProvider()

        # Subscribe provider to manager
        await progress_manager.subscribe(rich_provider)

        async with rich_provider:  # Start progress display
            # Simulate batch processing operation
            operation = create_progress_operation(
                description="Importing tracks from Last.fm",
                total_items=1000,
                source="lastfm",
                batch_size=50,
            )

            operation_id = await progress_manager.start_operation(operation)

            # Simulate batch processing with progress updates
            batch_size = 50
            total_items = 1000

            for batch_start in range(0, total_items, batch_size):
                batch_end = min(batch_start + batch_size, total_items)

                # Emit progress event for this batch
                await progress_manager.emit_progress(
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
            await progress_manager.complete_operation(
                operation_id, OperationStatus.COMPLETED
            )

            # Verify final state
            final_operation = await progress_manager.get_operation(operation_id)
            assert final_operation.status == OperationStatus.COMPLETED
            assert final_operation.duration_seconds > 0

        # Cleanup
        await progress_manager.shutdown()

        # Verify system cleaned up properly
        assert progress_manager.subscriber_count == 0
