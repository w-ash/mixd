"""Test RequestStartGate concurrent access patterns.

These tests specifically target the concurrency behavior of RequestStartGate
to ensure multiple tasks can wait concurrently without serialization.
"""

import asyncio
import threading
import time
from typing import Any

import pytest

from src.infrastructure.connectors._shared.request_start_gate import RequestStartGate


class TestRequestStartGateConcurrency:
    """Test concurrent access patterns for RequestStartGate."""

    @pytest.mark.asyncio
    async def test_concurrent_gate_wait_should_not_serialize(self):
        """Test that multiple concurrent wait() calls don't serialize.
        
        This test should FAIL with current implementation due to lock serialization bug.
        Multiple tasks calling wait() concurrently should delay concurrently, not sequentially.
        """
        gate = RequestStartGate(delay=0.1)  # 100ms delay between starts
        
        print("\n🔒 Testing concurrent RequestStartGate access")
        print(f"   Delay: {gate.delay}s between request starts")
        
        # Track timing and thread info for each wait call
        wait_results = []
        
        async def timed_wait(task_id: int) -> dict[str, Any]:
            """Call gate.wait() and track timing."""
            start_time = time.time()
            thread_id = threading.get_ident()
            
            print(f"   Task {task_id}: Starting wait() at {start_time:.3f} (thread {thread_id})")
            
            await gate.wait()  # This should not block other tasks from starting their waits
            
            end_time = time.time()
            wait_duration = end_time - start_time
            
            result = {
                "task_id": task_id,
                "thread_id": thread_id,
                "start_time": start_time,
                "end_time": end_time,
                "wait_duration": wait_duration,
            }
            
            print(f"   Task {task_id}: Completed wait() at {end_time:.3f} (waited {wait_duration:.3f}s)")
            return result
        
        # Create 5 concurrent tasks to test serialization
        num_tasks = 5
        overall_start = time.time()
        
        print(f"   Creating {num_tasks} concurrent wait() tasks...")
        
        # All tasks should start their wait() calls immediately
        tasks = [asyncio.create_task(timed_wait(i)) for i in range(num_tasks)]
        
        # Wait for all to complete
        wait_results = await asyncio.gather(*tasks)
        overall_duration = time.time() - overall_start
        
        print("\n📊 Concurrency Analysis:")
        print(f"   Total duration: {overall_duration:.3f}s")
        print(f"   Expected concurrent: ~{gate.delay:.3f}s (delay of longest wait)")
        print(f"   Expected serialized: ~{num_tasks * gate.delay:.3f}s ({num_tasks} × {gate.delay:.3f}s)")
        
        # Analyze results
        first_start = min(r["start_time"] for r in wait_results)
        last_start = max(r["start_time"] for r in wait_results)
        start_spread = last_start - first_start
        
        individual_waits = [r["wait_duration"] for r in wait_results]
        max_individual_wait = max(individual_waits)
        min_individual_wait = min(individual_waits)
        
        print(f"   Start time spread: {start_spread:.3f}s")
        print(f"   Individual wait times: {min_individual_wait:.3f}s - {max_individual_wait:.3f}s")
        
        # Check for serialization vs concurrent behavior
        # With proper concurrent delays, total time should be ~(num_tasks-1) * delay
        expected_concurrent_time = (num_tasks - 1) * gate.delay  # 4 * 0.1 = 0.4s
        expected_serialized_time = num_tasks * gate.delay  # 5 * 0.1 = 0.5s
        
        if abs(overall_duration - expected_concurrent_time) < 0.05:
            print("   ✅ CONCURRENT BEHAVIOR: Proper staggered delays")
            print(f"   Time matches expected concurrent pattern: {expected_concurrent_time:.3f}s")
        elif overall_duration > expected_serialized_time - 0.05:
            print("   🚨 SERIALIZATION DETECTED!")
            print(f"   Taking {overall_duration:.3f}s suggests lock held during sleep")
        else:
            print("   ⚠️ INTERMEDIATE: Some concurrency but not optimal")
        
        # Corrected assertion: total time should be close to staggered delay pattern
        # (num_tasks - 1) * delay, not just 2 * delay
        assert overall_duration <= expected_concurrent_time + 0.1, (
            f"RequestStartGate not working optimally: "
            f"{overall_duration:.3f}s (expected ≤ {expected_concurrent_time + 0.1:.3f}s)"
        )
        
        # Individual waits should follow the expected staggered pattern
        # First task: ~0ms wait, second: ~100ms wait, third: ~200ms wait, etc.
        for i, result in enumerate(wait_results):
            expected_wait = i * gate.delay
            actual_wait = result["wait_duration"]
            tolerance = gate.delay * 0.5  # 50ms tolerance
            
            print(f"   Task {i}: expected {expected_wait:.3f}s, actual {actual_wait:.3f}s")
            
            # Allow some tolerance for timing variations
            assert abs(actual_wait - expected_wait) < tolerance, (
                f"Task {i} wait time incorrect: {actual_wait:.3f}s "
                f"(expected ~{expected_wait:.3f}s ± {tolerance:.3f}s)"
            )

    @pytest.mark.asyncio
    async def test_gate_lock_acquisition_pattern(self):
        """Test the lock acquisition pattern to identify serialization."""
        gate = RequestStartGate(delay=0.05)  # 50ms for faster test
        
        lock_events = []
        
        # Monkey patch the lock to track acquisition/release
        original_acquire = gate._lock.acquire
        original_release = gate._lock.release
        
        async def tracked_acquire():
            timestamp = time.time()
            result = await original_acquire()
            lock_events.append({
                "event": "acquire", 
                "timestamp": timestamp,
                "thread": threading.get_ident()
            })
            return result
            
        def tracked_release():
            timestamp = time.time()
            result = original_release()
            lock_events.append({
                "event": "release", 
                "timestamp": timestamp,
                "thread": threading.get_ident()
            })
            return result
        
        gate._lock.acquire = tracked_acquire
        gate._lock.release = tracked_release
        
        try:
            # Create concurrent wait() calls
            tasks = [asyncio.create_task(gate.wait()) for _ in range(3)]
            await asyncio.gather(*tasks)
            
            print("\n🔍 Lock Event Analysis:")
            for event in lock_events:
                rel_time = event["timestamp"] - lock_events[0]["timestamp"]
                print(f"   {rel_time:.3f}s: {event['event']} (thread {event['thread']})")
            
            # Analyze lock holding duration
            acquire_times = [e for e in lock_events if e["event"] == "acquire"]
            release_times = [e for e in lock_events if e["event"] == "release"]
            
            if len(acquire_times) == len(release_times):
                for i, (acq, rel) in enumerate(zip(acquire_times, release_times, strict=False)):
                    hold_duration = rel["timestamp"] - acq["timestamp"]
                    print(f"   Lock hold #{i}: {hold_duration:.3f}s")
                    
                    # If lock is held longer than delay time, it's being held during sleep
                    if hold_duration > gate.delay * 0.8:
                        print(f"   🚨 Lock held during sleep: {hold_duration:.3f}s > {gate.delay:.3f}s")
                    else:
                        print(f"   ✅ Lock released quickly: {hold_duration:.3f}s")
            
        finally:
            # Restore original methods
            gate._lock.acquire = original_acquire
            gate._lock.release = original_release

    @pytest.mark.asyncio
    async def test_concurrent_realistic_lastfm_pattern(self):
        """Test with realistic Last.fm call pattern to reproduce the production issue."""
        gate = RequestStartGate(delay=0.222)  # ~4.5/sec like real Last.fm config
        
        print("\n🎵 Last.fm Realistic Concurrency Test")
        print(f"   Using {gate.delay:.3f}s delay (4.5 calls/sec)")
        
        async def simulate_lastfm_api_call(call_id: int) -> dict[str, Any]:
            """Simulate the real Last.fm API call pattern."""
            gate_start = time.time()
            
            # This is where the RequestStartGate serialization happens
            await gate.wait()
            
            gate_end = time.time()
            gate_duration = gate_end - gate_start
            
            # Simulate actual API call (without real network delay for test speed)
            api_start = time.time()
            await asyncio.sleep(0.1)  # Simulate 100ms API call
            api_end = time.time()
            
            return {
                "call_id": call_id,
                "gate_duration": gate_duration,
                "total_duration": api_end - gate_start,
                "gate_start": gate_start,
                "gate_end": gate_end,
                "api_start": api_start,
                "api_end": api_end,
            }
        
        # Test with 10 concurrent "Last.fm calls"
        num_calls = 10
        overall_start = time.time()
        
        tasks = [asyncio.create_task(simulate_lastfm_api_call(i)) for i in range(num_calls)]
        results = await asyncio.gather(*tasks)
        
        overall_duration = time.time() - overall_start
        
        print("\n📈 Last.fm Pattern Results:")
        print(f"   Total time: {overall_duration:.3f}s")
        print(f"   Expected concurrent: ~{(num_calls - 1) * gate.delay + 0.1:.3f}s")  
        print(f"   Expected serialized: ~{num_calls * (gate.delay + 0.1):.3f}s")
        
        # Analyze gate wait patterns
        gate_durations = [r["gate_duration"] for r in results]
        max_gate_wait = max(gate_durations)
        min_gate_wait = min(gate_durations)
        
        print(f"   Gate wait times: {min_gate_wait:.3f}s - {max_gate_wait:.3f}s")
        
        # Check for overlapping API calls (true concurrency)
        overlaps = 0
        for i, r1 in enumerate(results):
            for j, r2 in enumerate(results):
                if i != j:
                    if r1["api_start"] < r2["api_end"] and r1["api_end"] > r2["api_start"]:
                        overlaps += 1
        
        concurrent_api_calls = overlaps // 2 + 1 if overlaps > 0 else 1
        print(f"   Concurrent API calls: {concurrent_api_calls}")
        
        # The key test: should have concurrent API execution
        assert concurrent_api_calls > 1, (
            "No concurrent API execution detected - RequestStartGate is serializing"
        )
        
        return {
            "overall_duration": overall_duration,
            "concurrent_calls": concurrent_api_calls,
            "gate_durations": gate_durations,
        }