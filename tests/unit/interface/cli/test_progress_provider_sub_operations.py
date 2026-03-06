"""Unit tests for sub-operation display in RichProgressProvider.

Tests that sub-operations (with parent_operation_id metadata) get indented
descriptions and faster cleanup delays compared to top-level operations.
"""

import asyncio

from src.domain.entities.progress import OperationStatus, ProgressOperation
from src.interface.cli.progress_provider import RichProgressProvider


class TestSubOperationDisplay:
    """Tests sub-operation visual treatment in Rich progress bars."""

    async def test_sub_operation_gets_indented_description(self):
        provider = RichProgressProvider(show_rate=False)
        await provider.start_display()

        try:
            sub_op = ProgressOperation(
                operation_id="sub-1",
                description="Fetching metadata",
                total_items=100,
                metadata={"parent_operation_id": "parent-1"},
            )
            await provider.on_operation_started(sub_op)

            # Verify the operation was tracked
            assert "sub-1" in provider._operation_tasks

            # The Rich progress task description should be indented
            op_task = provider._operation_tasks["sub-1"]
            rich_task = provider._progress.tasks[op_task.task_id]
            assert rich_task.description.startswith("  \u21b3 ")
            assert "Fetching metadata" in rich_task.description
        finally:
            await provider.stop_display()

    async def test_top_level_operation_not_indented(self):
        provider = RichProgressProvider(show_rate=False)
        await provider.start_display()

        try:
            op = ProgressOperation(
                operation_id="top-1",
                description="Running workflow",
                total_items=50,
                metadata={},
            )
            await provider.on_operation_started(op)

            op_task = provider._operation_tasks["top-1"]
            rich_task = provider._progress.tasks[op_task.task_id]
            # Top-level operation should NOT have the indent prefix
            assert not rich_task.description.startswith("  \u21b3 ")
            assert rich_task.description == "Running workflow"
        finally:
            await provider.stop_display()

    async def test_sub_operation_gets_fast_cleanup(self):
        provider = RichProgressProvider(show_rate=False)
        await provider.start_display()

        try:
            sub_op = ProgressOperation(
                operation_id="sub-1",
                description="Fetching metadata",
                total_items=10,
                metadata={"parent_operation_id": "parent-1"},
            )
            await provider.on_operation_started(sub_op)

            # Complete the sub-operation
            await provider.on_operation_completed("sub-1", OperationStatus.COMPLETED)

            # The sub-operation should still be tracked (cleanup is delayed)
            assert "sub-1" in provider._operation_tasks
            assert not provider._operation_tasks["sub-1"].is_active

            # Wait for the fast cleanup (0.5s) plus a small margin
            await asyncio.sleep(0.7)

            # After 0.7s the sub-operation should be cleaned up (0.5s delay)
            assert "sub-1" not in provider._operation_tasks
        finally:
            await provider.stop_display()
