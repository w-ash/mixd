"""Test cleanup of async tasks in RichProgressProvider.

TDD test to ensure cleanup tasks don't leak when display is stopped.
"""

import asyncio

from src.domain.entities.progress import OperationStatus, ProgressOperation
from src.interface.cli.progress_provider import RichProgressProvider


class TestRichProgressProviderCleanup:
    """Test that RichProgressProvider properly cleans up async tasks."""

    async def test_cleanup_tasks_are_cancelled_on_stop(self):
        """Test that pending cleanup tasks are cancelled when display stops.

        This test reproduces the issue where fire-and-forget cleanup tasks
        are left pending when the progress context exits, causing asyncio
        to complain about destroyed pending tasks.
        """
        # Create provider
        provider = RichProgressProvider(show_rate=False)

        # Start display
        await provider.start_display()

        # Create and complete an operation (triggers cleanup task)
        operation = ProgressOperation(
            operation_id="test-op-1",
            description="Test Operation",
            total_items=100,
        )

        # Notify operation started
        await provider.on_operation_started(operation)

        # Simulate operation completing
        await provider.on_operation_completed("test-op-1", OperationStatus.COMPLETED)

        # Immediately stop display (before cleanup task finishes its 2-second delay)
        await provider.stop_display()

        # Give event loop a chance to process any remaining callbacks
        await asyncio.sleep(0.1)

        # Check that no tasks are left pending
        # This will fail if cleanup tasks aren't properly cancelled
        pending_tasks = [
            task
            for task in asyncio.all_tasks()
            if not task.done() and task.get_coro().__name__ == "_cleanup_completed_task"
        ]

        assert len(pending_tasks) == 0, (
            f"Found {len(pending_tasks)} pending cleanup tasks that should have been cancelled"
        )

    async def test_multiple_operations_cleanup_cancelled(self):
        """Test that multiple pending cleanup tasks are all cancelled."""
        provider = RichProgressProvider(show_rate=False)
        await provider.start_display()

        # Create and complete multiple operations
        for i in range(5):
            operation = ProgressOperation(
                operation_id=f"test-op-{i}",
                description=f"Test Operation {i}",
                total_items=100,
            )
            await provider.on_operation_started(operation)

            await provider.on_operation_completed(
                f"test-op-{i}", OperationStatus.COMPLETED
            )

        # Stop display immediately
        await provider.stop_display()

        # Give event loop a chance to process
        await asyncio.sleep(0.1)

        # Verify no cleanup tasks are left pending
        pending_cleanup_tasks = [
            task
            for task in asyncio.all_tasks()
            if not task.done() and task.get_coro().__name__ == "_cleanup_completed_task"
        ]

        assert len(pending_cleanup_tasks) == 0, (
            f"Found {len(pending_cleanup_tasks)} pending cleanup tasks, "
            f"expected 0 (all should be cancelled)"
        )
