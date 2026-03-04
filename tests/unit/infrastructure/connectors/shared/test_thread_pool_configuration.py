"""Test thread pool configuration for improved concurrency.

Tests the standard asyncio approach of configuring the default executor
for better concurrency with blocking operations.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import threading
import time
from typing import Any

import pytest

pytestmark = pytest.mark.diagnostic


class TestThreadPoolConfiguration:
    """Test thread pool configuration using standard asyncio patterns."""

    async def test_default_executor_configuration(self):
        """Test configuring default executor for better concurrency."""

        def blocking_call(call_id: int, duration: float = 0.5) -> dict[str, Any]:
            """Mock blocking call."""
            start_time = time.time()
            thread_id = threading.get_ident()
            time.sleep(duration)
            end_time = time.time()

            return {
                "call_id": call_id,
                "thread_id": thread_id,
                "duration": end_time - start_time,
                "start_time": start_time,
                "end_time": end_time,
            }

        # Test with custom executor - 20 threads for 20 concurrent calls
        loop = asyncio.get_running_loop()
        original_executor = loop._default_executor

        try:
            # Configure custom executor with more threads
            custom_executor = ThreadPoolExecutor(max_workers=20)
            loop.set_default_executor(custom_executor)

            print("\n🔧 Testing with custom executor (20 threads)")

            # Test 20 concurrent calls
            num_calls = 20
            call_duration = 0.5

            overall_start = time.time()

            # Create tasks (same pattern as our Last.fm calls)
            tasks = [
                asyncio.create_task(asyncio.to_thread(blocking_call, i, call_duration))
                for i in range(num_calls)
            ]

            # Wait for all to complete
            results = await asyncio.gather(*tasks)
            overall_duration = time.time() - overall_start

            # Analyze results
            unique_threads = len({r["thread_id"] for r in results})

            print(f"   Total duration: {overall_duration:.3f}s")
            print(f"   Expected concurrent: ~{call_duration:.3f}s")
            print(f"   Unique threads used: {unique_threads}")

            # With 20 threads, all 20 calls should run concurrently
            assert overall_duration < call_duration * 1.5, (
                f"Not concurrent: {overall_duration:.3f}s"
            )
            assert unique_threads >= 15, f"Not enough threads used: {unique_threads}"

            print("   ✅ True concurrency achieved with custom executor")

        finally:
            # Clean up custom executor
            if "custom_executor" in locals():
                custom_executor.shutdown(wait=False)
            # Don't restore None executor - let asyncio create a new default one

    async def test_lastfm_scale_simulation_with_custom_executor(self):
        """Test Last.fm scale (50 calls) with custom executor."""

        def simulate_lastfm_call(call_id: int) -> dict[str, Any]:
            """Simulate Last.fm call with realistic timing."""
            import random

            duration = random.uniform(0.3, 0.8)  # Shorter for test speed

            start_time = time.time()
            thread_id = threading.get_ident()
            time.sleep(duration)
            end_time = time.time()

            return {
                "call_id": call_id,
                "thread_id": thread_id,
                "duration": end_time - start_time,
                "start_time": start_time,
                "end_time": end_time,
            }

        loop = asyncio.get_running_loop()
        original_executor = loop._default_executor

        try:
            # Configure executor for 50 concurrent Last.fm calls
            custom_executor = ThreadPoolExecutor(max_workers=50)
            loop.set_default_executor(custom_executor)

            print("\n🎵 Last.fm Scale Test (50 calls with 50-thread executor)")

            num_calls = 50
            overall_start = time.time()

            # Create all tasks at once (same as APIBatchProcessor)
            tasks = [
                asyncio.create_task(asyncio.to_thread(simulate_lastfm_call, i))
                for i in range(num_calls)
            ]

            print(f"   Created {num_calls} tasks at once")

            # Process as they complete (same as as_completed pattern)
            completed_count = 0
            results = []

            for completed_task in asyncio.as_completed(tasks):
                result = await completed_task
                results.append(result)
                completed_count += 1

                if completed_count % 10 == 0:
                    elapsed = time.time() - overall_start
                    print(
                        f"   {completed_count}/{num_calls} completed at {elapsed:.1f}s"
                    )

            total_time = time.time() - overall_start
            unique_threads = len({r["thread_id"] for r in results})

            print("\n📈 Scale Test Results:")
            print(f"   Total time: {total_time:.1f}s")
            print(f"   Unique threads: {unique_threads}")
            print("   Expected concurrent: ~0.8s (max call duration)")
            print("   Expected limited (10 threads): ~4s+")

            # With 50 threads, should complete in ~1s (duration of longest call)
            if total_time < 2.0:
                print("   ✅ EXCELLENT: True concurrency achieved")
                assert unique_threads >= 40, (
                    f"Should use many threads: {unique_threads}"
                )
            else:
                print(f"   ⚠️  Still some bottleneck: {total_time:.1f}s")

            # Should definitely be much faster than 10-thread limit
            assert total_time < 3.0, f"Custom executor not effective: {total_time:.1f}s"

        finally:
            # Clean up - don't restore None executor
            if "custom_executor" in locals():
                custom_executor.shutdown(wait=False)

    async def test_thread_pool_sizing_recommendations(self):
        """Test different thread pool sizes to find optimal configuration."""

        def blocking_call(call_id: int) -> dict[str, Any]:
            """Simple blocking call for sizing test."""
            time.sleep(0.1)  # Quick for testing
            return {
                "call_id": call_id,
                "thread_id": threading.get_ident(),
            }

        loop = asyncio.get_running_loop()
        original_executor = loop._default_executor

        # Test different pool sizes
        pool_sizes = [10, 25, 50, 100]
        num_calls = 50

        results_by_size = {}

        print(f"\n🔬 Thread Pool Sizing Analysis ({num_calls} calls)")

        for pool_size in pool_sizes:
            try:
                custom_executor = ThreadPoolExecutor(max_workers=pool_size)
                loop.set_default_executor(custom_executor)

                start_time = time.time()

                tasks = [
                    asyncio.create_task(asyncio.to_thread(blocking_call, i))
                    for i in range(num_calls)
                ]

                call_results = await asyncio.gather(*tasks)
                duration = time.time() - start_time
                unique_threads = len({r["thread_id"] for r in call_results})

                results_by_size[pool_size] = {
                    "duration": duration,
                    "unique_threads": unique_threads,
                }

                print(
                    f"   {pool_size:3d} threads: {duration:.3f}s, used {unique_threads} threads"
                )

            finally:
                custom_executor.shutdown(wait=False)

        # No need to restore - let asyncio create a new default executor if needed

        print("\n💡 Sizing Recommendations:")

        # Find the point of diminishing returns
        best_duration = min(r["duration"] for r in results_by_size.values())

        for size, result in results_by_size.items():
            efficiency = best_duration / result["duration"]
            print(f"   {size:3d} threads: {efficiency:.2f}x efficiency")

            if efficiency > 0.95 and size >= 50:
                print(
                    f"   ✅ Recommended: {size} threads (good efficiency for 50+ calls)"
                )
                break

        return results_by_size
