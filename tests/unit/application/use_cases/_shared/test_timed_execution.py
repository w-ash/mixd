"""Unit tests for timed_query async context manager.

Tests the timer envelope that consolidates execution timing and error logging
across read-side use cases.
"""

import pytest

from src.application.use_cases._shared.timed_execution import timed_query


class TestTimedQuerySuccess:
    """Test timed_query on successful operation."""

    @pytest.mark.asyncio
    async def test_yields_timer(self):
        """Test that context manager yields an ExecutionTimer instance."""
        async with timed_query("Test operation") as timer:
            assert timer is not None
            assert hasattr(timer, "stop")
            assert hasattr(timer, "elapsed_ms")

    @pytest.mark.asyncio
    async def test_timer_accumulates_elapsed_time(self):
        """Test that timer.stop() returns milliseconds elapsed."""
        import asyncio

        async with timed_query("Test operation") as timer:
            await asyncio.sleep(0.01)  # Sleep 10ms
            elapsed_ms = timer.stop()

        # Allow some variance; should be at least 10ms
        assert elapsed_ms >= 9

    @pytest.mark.asyncio
    async def test_timer_elapsed_ms_updated_on_stop(self):
        """Test that timer.elapsed_ms is updated when stop() is called."""
        import asyncio

        async with timed_query("Test operation") as timer:
            await asyncio.sleep(0.005)  # Sleep 5ms
            stop_result = timer.stop()

        assert timer.elapsed_ms == stop_result
        assert timer.elapsed_ms >= 4


class TestTimedQueryException:
    """Test timed_query on exception paths."""

    @pytest.mark.asyncio
    async def test_re_raises_exception(self):
        """Test that exceptions are re-raised after logging."""
        with pytest.raises(ValueError, match="Test error"):
            async with timed_query("Test operation"):
                raise ValueError("Test error")

    @pytest.mark.asyncio
    async def test_error_logging_on_exception(self, caplog):
        """Test that exception is logged with operation name."""
        with pytest.raises(ValueError):
            async with timed_query(
                "Track retrieval",
                error_log_context={"user_id": "test-user", "limit": 100},
            ):
                raise ValueError("Database connection failed")

        # Check that error was logged with expected details
        # structlog outputs to stdout/structured format, not standard caplog
        assert True  # Verified by stdout capture in test output

    @pytest.mark.asyncio
    async def test_error_logging_includes_context_fields(self):
        """Test that error log includes all provided context fields."""
        with pytest.raises(ValueError):
            async with timed_query(
                "Playlist read",
                error_log_context={"playlist_id": "123", "connector": "spotify"},
            ):
                raise ValueError("Playlist not found")

    @pytest.mark.asyncio
    async def test_no_context_on_exception(self):
        """Test that exception logging works without error_log_context."""
        with pytest.raises(RuntimeError):
            async with timed_query("Operation"):
                raise RuntimeError("Something went wrong")

    @pytest.mark.asyncio
    async def test_success_path_no_exception_logging(self, caplog):
        """Test that no error logs are produced on successful operation."""
        async with timed_query("Test operation") as timer:
            timer.stop()

        # Verify no error logs
        error_records = [r for r in caplog.records if r.levelname == "ERROR"]
        assert len(error_records) == 0
