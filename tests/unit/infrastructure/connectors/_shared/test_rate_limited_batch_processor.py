"""Unit tests for progress_callback parameter on RateLimitedBatchProcessor.process_batch().

Verifies that the optional progress callback is invoked with correct (completed, total, message)
tuples after each item completes, and that omitting it causes no errors.
"""

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
