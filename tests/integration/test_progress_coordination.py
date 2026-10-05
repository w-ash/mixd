"""Integration tests for CLI progress coordination.

Covers ``progress_coordination_context`` (subscribes a RichProgressSubscriber
to the global broker and moves console logging onto the Live console for the
block) and RichProgressSubscriber's rendering of concurrent operations.
"""

import asyncio
import io
import logging

import pytest

from src.application.services.progress_broker import get_progress_broker
from src.domain.entities.progress import (
    OperationStatus,
    ProgressEvent,
    ProgressOperation,
    ProgressStatus,
    create_progress_operation,
)
from src.interface.cli.console import get_console, progress_coordination_context
from src.interface.cli.progress_subscriber import RichProgressSubscriber


@pytest.fixture
def console_handler(monkeypatch: pytest.MonkeyPatch) -> logging.Handler:
    """Give the root logger exactly one standard console handler."""
    monkeypatch.setattr("src.config.logging._saved_console_handler", None)
    handler = logging.StreamHandler(io.StringIO())
    root = logging.getLogger()
    monkeypatch.setattr(root, "handlers", [handler])
    return handler


class TestProgressConsoleCoordination:
    """progress_coordination_context wiring."""

    async def test_live_context_subscribes_and_swaps_console_then_restores(
        self, console_handler: logging.Handler
    ):
        root = logging.getLogger()
        before = set(root.handlers)
        broker = get_progress_broker()

        async with progress_coordination_context(show_live=True) as context:
            provider = context.provider
            assert context.get_progress_broker() is broker
            assert context.console is provider.get_console()
            # Logs must not bypass the Live console while bars are pinned.
            assert console_handler not in root.handlers

            inside = await broker.start_operation(
                create_progress_operation("Inside", total_items=1)
            )
            assert inside in provider._operation_tasks
            await broker.complete_operation(inside, OperationStatus.COMPLETED)

        assert set(root.handlers) == before
        after = await broker.start_operation(
            create_progress_operation("After", total_items=1)
        )
        try:
            assert after not in provider._operation_tasks
        finally:
            await broker.complete_operation(after, OperationStatus.COMPLETED)

    async def test_simple_console_context_without_progress(
        self, console_handler: logging.Handler
    ):
        root = logging.getLogger()
        before = list(root.handlers)

        async with progress_coordination_context(show_live=False) as context:
            assert context.console is get_console()
            assert context.get_progress_broker() is None
            assert console_handler in root.handlers

        assert root.handlers == before


class TestRichProgressSubscriberRendering:
    """What the pinned bars show once operations finish."""

    async def test_concurrent_operations_each_end_in_their_own_final_state(
        self, console_handler: logging.Handler
    ):
        provider = RichProgressSubscriber()
        outcomes = [
            OperationStatus.COMPLETED,
            OperationStatus.FAILED,
            OperationStatus.CANCELLED,
        ]
        operations = [
            ProgressOperation(
                operation_id=f"op_{i}", description=f"Operation {i}", total_items=50
            )
            for i in range(3)
        ]

        async def drive(operation: ProgressOperation, outcome: OperationStatus):
            for current in (10, 20, 30):
                await provider.on_progress_event(
                    ProgressEvent(
                        operation_id=operation.operation_id,
                        current=current,
                        total=50,
                        message=f"{operation.description}: step {current}",
                        status=ProgressStatus.IN_PROGRESS,
                    )
                )
                await asyncio.sleep(0)
            await provider.on_operation_completed(operation.operation_id, outcome)

        try:
            await provider.start_display()
            for operation in operations:
                await provider.on_operation_started(operation)
            await asyncio.gather(*map(drive, operations, outcomes, strict=True))

            tasks = {
                op_id: next(
                    t for t in provider._progress.tasks if t.id == tracked.task_id
                )
                for op_id, tracked in provider._operation_tasks.items()
            }
        finally:
            await provider.stop_display()

        # A completed bar fills to its total; failed and cancelled bars stay
        # where the last event left them. Each shows its own outcome.
        assert tasks["op_0"].completed == 50
        assert "Operation 0" in tasks["op_0"].description
        assert "Completed" in tasks["op_0"].description
        assert tasks["op_1"].completed == 30
        assert "Operation 1" in tasks["op_1"].description
        assert "Failed" in tasks["op_1"].description
        assert tasks["op_2"].completed == 30
        assert "Operation 2" in tasks["op_2"].description
        assert "Cancelled" in tasks["op_2"].description
