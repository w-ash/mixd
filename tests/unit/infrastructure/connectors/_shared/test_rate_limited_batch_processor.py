"""Unit tests for RateLimitedBatchProcessor.

Verifies progress callback, concurrent dict safety, and task cleanup.
"""

import asyncio

from src.infrastructure.connectors._shared.rate_limited_batch_processor import (
    RateLimitedBatchProcessor,
)


def _make_processor() -> RateLimitedBatchProcessor:
    """Build a fast processor suitable for unit tests."""
    return RateLimitedBatchProcessor(
        rate_per_second=100,
        connector_name="test",
        max_concurrent_tasks=5,
    )


class TestProgressCallbackHappyPath:
    """Tests that the callback fires with correct counts for every item."""

    async def test_progress_callback_called_with_correct_counts(self):
        processor = _make_processor()
        items = [1, 2, 3]

        async def process_item(item: int) -> int:
            return item * 2

        callback_calls: list[tuple[int, int, str]] = []

        async def mock_callback(completed: int, total: int, message: str) -> None:
            callback_calls.append((completed, total, message))

        results: list[tuple[str, int | None]] = []
        async for item_id, result in processor.process_batch(
            items, process_item, progress_callback=mock_callback
        ):
            results.append((item_id, result))

        # All 3 items should have been processed
        assert len(results) == 3

        # Callback should have been called 3 times
        assert len(callback_calls) == 3

        # Verify counts are sequential 1..3, each with total=3
        completed_counts = sorted(c[0] for c in callback_calls)
        assert completed_counts == [1, 2, 3]

        for completed, total, message in callback_calls:
            assert total == 3
            assert f"{completed}/3" in message


class TestProgressCallbackEdgeCases:
    """Tests backward compatibility and edge cases for progress_callback."""

    async def test_progress_callback_none_backward_compat(self):
        processor = _make_processor()
        items = [10, 20]

        async def process_item(item: int) -> int:
            return item + 1

        results: list[tuple[str, int | None]] = []
        async for item_id, result in processor.process_batch(
            items, process_item, progress_callback=None
        ):
            results.append((item_id, result))

        # Should complete without errors
        assert len(results) == 2


class TestConcurrentDictSafety:
    """Tests that _collect_results snapshots the dict to avoid RuntimeError."""

    async def test_concurrent_writes_do_not_raise_runtime_error(self):
        """Slow process func forces concurrent dict writes during collection."""
        processor = _make_processor()
        items = list(range(10))

        async def slow_process(item: int) -> int:
            # Stagger completions to maximize concurrent dict mutation
            await asyncio.sleep(0.01 * item)
            return item

        results: list[tuple[str, int | None]] = []
        async for item_id, result in processor.process_batch(items, slow_process):
            results.append((item_id, result))

        assert len(results) == 10


class TestTaskCleanup:
    """Tests that orphaned work-item tasks are cancelled on shutdown."""

    async def test_running_tasks_cancelled_on_early_exit(self):
        """Tasks still running when consumer breaks are cancelled in finally."""
        processor = _make_processor()
        items = list(range(5))

        async def blocking_process(item: int) -> int:
            # First item completes fast, rest block until cancelled
            if item == 0:
                return item
            await asyncio.sleep(60)
            return item

        async for _item_id, _result in processor.process_batch(items, blocking_process):
            # Break after first result — remaining tasks should be cancelled
            break

        # Give the finally block a tick to cancel tasks
        await asyncio.sleep(0.05)
        assert len(processor.running_tasks) == 0
