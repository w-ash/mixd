"""Unit tests for sub-operation display in RichProgressSubscriber.

Tests that sub-operations (with parent_operation_id metadata) get indented
descriptions and faster cleanup delays compared to top-level operations.
"""

import asyncio

import pytest

from src.domain.entities.progress import OperationStatus, ProgressOperation
from src.interface.cli.progress_subscriber import RichProgressSubscriber


class TestSubOperationDisplay:
    """Tests sub-operation visual treatment in Rich progress bars."""

    async def test_sub_operation_gets_indented_description(self):
        provider = RichProgressSubscriber(show_rate=False)
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
        provider = RichProgressSubscriber(show_rate=False)
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

    async def test_sub_operation_cleanup_delay_is_shorter_than_top_level(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        # The clock is the boundary: record each cleanup delay, then return at
        # once so removal can be observed without waiting in real time.
        real_sleep = asyncio.sleep
        delays: list[float] = []

        async def _recording_sleep(seconds: float) -> None:
            delays.append(seconds)
            await real_sleep(0)

        provider = RichProgressSubscriber(show_rate=False)
        await provider.start_display()
        monkeypatch.setattr(asyncio, "sleep", _recording_sleep)

        try:
            await provider.on_operation_started(
                ProgressOperation(
                    operation_id="top-1",
                    description="Running workflow",
                    total_items=10,
                )
            )
            await provider.on_operation_started(
                ProgressOperation(
                    operation_id="sub-1",
                    description="Fetching metadata",
                    total_items=10,
                    metadata={"parent_operation_id": "top-1"},
                )
            )

            await provider.on_operation_completed("sub-1", OperationStatus.COMPLETED)
            # Cleanup is deferred: the finished bar stays visible until it runs.
            assert "sub-1" in provider._operation_tasks
            assert not provider._operation_tasks["sub-1"].is_active
            await real_sleep(0)
            await real_sleep(0)
            sub_delays = [d for d in delays if d > 0]
            assert "sub-1" not in provider._operation_tasks

            await provider.on_operation_completed("top-1", OperationStatus.COMPLETED)
            await real_sleep(0)
            await real_sleep(0)
            top_delays = [d for d in delays if d > 0][len(sub_delays) :]
            assert "top-1" not in provider._operation_tasks

            assert len(sub_delays) == 1
            assert len(top_delays) == 1
            assert 0 < sub_delays[0] < top_delays[0]
            assert sub_delays[0] <= 0.5
        finally:
            monkeypatch.undo()
            await provider.stop_display()
