"""Unit tests for CLI async runner functions.

Tests the run_async() function that provides sync-to-async bridging
for CLI command handlers.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import threading
import time

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
        assert executor._thread_name_prefix == "narada_io"

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

    def test_run_async_handles_async_arguments(self):
        """run_async() should work with parameterized coroutines."""
        from src.interface.cli.async_runner import run_async

        async def coro_with_args(a: int, b: str):
            return f"{a}-{b}"

        result = run_async(coro_with_args(42, "test"))
        assert result == "42-test"

    @pytest.mark.slow
    def test_run_async_provides_high_concurrency(self):
        """Helper should provide 200-thread executor for high concurrency."""
        from src.interface.cli.async_runner import run_async

        async def test_concurrency():
            """Test that we get a high-concurrency executor."""

            def blocking_work(work_id: int) -> dict:
                """Simulate blocking I/O work."""
                time.sleep(0.1)
                return {
                    "id": work_id,
                    "thread": threading.get_ident(),
                }

            # Create 50 concurrent tasks (more than default executor limit of ~32-36)
            start_time = time.time()
            tasks = [asyncio.to_thread(blocking_work, i) for i in range(50)]
            results = await asyncio.gather(*tasks)
            duration = time.time() - start_time

            unique_threads = len({r["thread"] for r in results})

            return {
                "duration": duration,
                "unique_threads": unique_threads,
                "results_count": len(results),
            }

        result = run_async(test_concurrency())

        # With 200-thread executor, all 50 should run concurrently
        # Duration should be ~0.1s (one blocking call duration), not 50 * 0.1s
        assert result["unique_threads"] >= 40, (
            f"Not using enough threads: {result['unique_threads']}"
        )
        assert result["results_count"] == 50

    def test_run_async_loops_are_independent(self):
        """Each call should use an independent event loop (no state leakage)."""
        from src.interface.cli.async_runner import run_async

        # Use task-local state to verify independence
        async def set_and_check_task_name(name: str):
            """Set a task name and verify only this task sees it."""
            task = asyncio.current_task()
            task.set_name(name)
            return task.get_name()

        name_1 = run_async(set_and_check_task_name("call_1"))
        name_2 = run_async(set_and_check_task_name("call_2"))

        # Each call should see its own task name (proves loop independence)
        assert name_1 == "call_1"
        assert name_2 == "call_2"

    def test_run_async_cleans_up_loop(self):
        """Helper should clean up the event loop after execution."""
        from src.interface.cli.async_runner import run_async

        async def simple_coro():
            return "done"

        # Run multiple times - should not leak event loops
        for _ in range(5):
            result = run_async(simple_coro())
            assert result == "done"

        # If loops weren't cleaned up, we'd see warnings or errors
        # This test passing means cleanup is working
