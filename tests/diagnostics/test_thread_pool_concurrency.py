"""Thread pool concurrency diagnostic tests.

Tests to verify if asyncio.to_thread() thread pool limitations are causing
sequential execution instead of true concurrency.
"""

import asyncio
import threading
import time
from typing import Any

import pytest


class TestThreadPoolConcurrency:
    """Diagnostic tests for asyncio thread pool concurrency limits."""

    @pytest.mark.asyncio
    async def test_asyncio_to_thread_concurrency_limit(self):
        """Test if asyncio.to_thread() has concurrency limitations.

        This test simulates our Last.fm API call pattern with mock blocking calls
        to verify if thread pool exhaustion is causing sequential execution.
        """

        def blocking_call(call_id: int, duration: float = 0.5) -> dict[str, Any]:
            """Mock blocking call simulating Last.fm API request."""
            start_time = time.time()
            thread_id = threading.get_ident()

            # Simulate network I/O delay
            time.sleep(duration)

            end_time = time.time()
            return {
                "call_id": call_id,
                "thread_id": thread_id,
                "start_time": start_time,
                "end_time": end_time,
                "duration": end_time - start_time,
            }

        # Test with 10 concurrent calls (same pattern as our Last.fm batch)
        num_calls = 10
        call_duration = 0.5  # 500ms each

        print(
            f"\n🧪 Testing {num_calls} concurrent blocking calls ({call_duration}s each)"
        )

        # Create tasks using same pattern as our APIBatchProcessor
        overall_start = time.time()
        tasks = [
            asyncio.create_task(asyncio.to_thread(blocking_call, i, call_duration))
            for i in range(num_calls)
        ]

        print(f"   ✓ Created {len(tasks)} tasks at {time.time():.3f}")

        # Collect results as they complete (same as our as_completed pattern)
        results = []
        for completed_task in asyncio.as_completed(tasks):
            result = await completed_task
            results.append(result)

            # Log each completion for timing analysis
            elapsed = time.time() - overall_start
            print(
                f"   ✓ Call {result['call_id']} completed at {elapsed:.3f}s (thread {result['thread_id']})"
            )

        overall_duration = time.time() - overall_start

        # Analyze concurrency patterns
        unique_threads = len({r["thread_id"] for r in results})

        # Check for overlapping execution
        overlapping_calls = 0
        for i, result1 in enumerate(results):
            for j, result2 in enumerate(results):
                if i != j:
                    # Check if calls overlapped in time
                    if (
                        result1["start_time"] < result2["end_time"]
                        and result1["end_time"] > result2["start_time"]
                    ):
                        overlapping_calls += 1

        max_concurrent = overlapping_calls // 2 + 1 if overlapping_calls > 0 else 1

        print("\n📊 Concurrency Analysis:")
        print(f"   Total duration: {overall_duration:.3f}s")
        print(f"   Expected concurrent: {call_duration:.3f}s")
        print(f"   Expected sequential: {num_calls * call_duration:.3f}s")
        print(f"   Unique threads used: {unique_threads}")
        print(f"   Max concurrent calls: {max_concurrent}")
        print(
            f"   Efficiency ratio: {(num_calls * call_duration) / overall_duration:.2f}"
        )

        # Assertions to detect thread pool limitations
        if overall_duration > call_duration * 2:
            print("   🚨 SEQUENTIAL EXECUTION DETECTED!")
            print(f"   Taking {overall_duration:.3f}s instead of ~{call_duration:.3f}s")
            print(
                f"   This suggests thread pool exhaustion with only {unique_threads} threads"
            )
        else:
            print("   ✅ True concurrency achieved")

        # This test will help us understand the current behavior
        # We expect it to show limited concurrency due to small default thread pool
        return {
            "total_duration": overall_duration,
            "unique_threads": unique_threads,
            "max_concurrent": max_concurrent,
            "results": results,
        }

    @pytest.mark.asyncio
    async def test_thread_pool_capacity_detection(self):
        """Test to detect the actual thread pool capacity."""

        def quick_blocking_call(call_id: int) -> dict[str, Any]:
            """Very quick blocking call to detect thread capacity."""
            return {
                "call_id": call_id,
                "thread_id": threading.get_ident(),
                "timestamp": time.time(),
            }

        # Create many quick tasks to see how many unique threads we get
        num_tasks = 50

        tasks = [
            asyncio.create_task(asyncio.to_thread(quick_blocking_call, i))
            for i in range(num_tasks)
        ]

        results = await asyncio.gather(*tasks)
        unique_threads = {r["thread_id"] for r in results}

        print("\n🔍 Thread Pool Capacity Detection:")
        print(f"   Tasks created: {num_tasks}")
        print(f"   Unique threads used: {len(unique_threads)}")
        print(f"   Thread IDs: {sorted(unique_threads)}")

        # This will show us the actual thread pool size
        assert len(unique_threads) > 0

        if len(unique_threads) < 20:
            print(
                f"   ⚠️ Limited thread pool detected: only {len(unique_threads)} threads"
            )
        else:
            print(f"   ✅ Adequate thread pool: {len(unique_threads)} threads")

        return len(unique_threads)

    @pytest.mark.asyncio
    async def test_lastfm_pattern_simulation(self):
        """Simulate exact Last.fm API call pattern from logs."""

        def simulate_lastfm_call(call_id: int) -> dict[str, Any]:
            """Simulate Last.fm call with realistic 3-5s duration."""
            import random

            duration = random.uniform(3.0, 5.0)  # Real Last.fm timing

            start_time = time.time()
            thread_id = threading.get_ident()

            time.sleep(duration)

            end_time = time.time()
            return {
                "call_id": call_id,
                "thread_id": thread_id,
                "start_time": start_time,
                "end_time": end_time,
                "actual_duration": end_time - start_time,
            }

        # Test with 10 calls (smaller than 50 for faster test)
        num_calls = 10

        print(f"\n🎵 Last.fm Pattern Simulation ({num_calls} calls)")

        overall_start = time.time()

        # Same pattern as APIBatchProcessor
        tasks = [
            asyncio.create_task(asyncio.to_thread(simulate_lastfm_call, i))
            for i in range(num_calls)
        ]

        print(f"   All {num_calls} tasks created at once")

        # Track completions
        completed_count = 0
        for completed_task in asyncio.as_completed(tasks):
            result = await completed_task
            completed_count += 1
            elapsed = time.time() - overall_start
            print(
                f"   Call {result['call_id']} completed #{completed_count}/{num_calls} at {elapsed:.1f}s"
            )

        total_time = time.time() - overall_start

        print("\n📈 Simulation Results:")
        print(f"   Total time: {total_time:.1f}s")
        print("   Expected concurrent: ~5s (longest call)")
        print("   Expected sequential: ~40s (10 × 4s avg)")

        if total_time > 15:
            print(
                f"   🚨 LIKELY SEQUENTIAL: {total_time:.1f}s suggests thread pool exhaustion"
            )
        else:
            print(
                f"   ✅ GOOD CONCURRENCY: {total_time:.1f}s suggests parallel execution"
            )

        return total_time
