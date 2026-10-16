"""Unit tests for CLI async runner functions.

Tests the run_async() function that provides sync-to-async bridging
for CLI command handlers.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from src.config import settings


class TestExecutorHelperFunctions:
    """Test the CLI async runner functions."""

    def test_create_executor_for_connectors_returns_configured_executor(self):
        """create_executor_for_connectors() should return ThreadPoolExecutor with correct config."""
        from src.interface.cli.async_runner import create_executor_for_connectors

        executor = create_executor_for_connectors()

        assert isinstance(executor, ThreadPoolExecutor)
        assert executor._max_workers == settings.api.lastfm.concurrency
        assert executor._thread_name_prefix == "mixd_io"

        # Clean up
        executor.shutdown(wait=False)

    def test_run_async_executes_coroutine(self):
        """run_async() should execute async functions."""
        from src.interface.cli.async_runner import run_async

        async def test_coro():
            return "test_result"

        result = run_async(test_coro())
        assert result == "test_result"

    def test_run_async_passes_exceptions(self):
        """run_async() should propagate exceptions."""
        from src.interface.cli.async_runner import run_async

        async def failing_coro():
            raise ValueError("Test error")

        with pytest.raises(ValueError, match="Test error"):
            run_async(failing_coro())

    def test_run_async_installs_connector_executor_as_loop_default(self):
        """Blocking work in run_async() runs on the connector pool, not asyncio's default pool.

        The barrier needs more parties than asyncio's default pool holds (at most 32
        workers), so it releases only when the connector pool is the loop default.
        The barrier waits for every party, so machine load cannot fail the test.
        """
        from src.interface.cli.async_runner import run_async

        parties = 40
        assert settings.api.lastfm.concurrency >= parties
        barrier = threading.Barrier(parties, timeout=10)

        def blocking_work() -> str:
            barrier.wait()
            return threading.current_thread().name

        async def fan_out() -> list[str]:
            return list(
                await asyncio.gather(
                    *(asyncio.to_thread(blocking_work) for _ in range(parties))
                )
            )

        thread_names = run_async(fan_out())

        assert len(thread_names) == parties
        assert all(name.startswith("mixd_io") for name in thread_names), thread_names

    def test_run_async_closes_its_event_loop(self):
        """Each call closes the loop it ran on, so repeated CLI calls leak none."""
        from src.interface.cli.async_runner import run_async

        async def capture_loop() -> asyncio.AbstractEventLoop:
            return asyncio.get_running_loop()

        first = run_async(capture_loop())
        second = run_async(capture_loop())

        assert first.is_closed()
        assert second.is_closed()
        assert first is not second
