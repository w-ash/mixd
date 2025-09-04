"""Test to reproduce RequestStartGate drift issue with connector reuse pattern."""

import asyncio
import time

import pytest

from src.infrastructure.connectors._shared.request_start_gate import RequestStartGate


class TestRequestStartGateReuseIssue:
    """Test RequestStartGate drift when reused across multiple batch operations."""

    @pytest.mark.asyncio
    async def test_gate_reuse_causes_time_drift(self):
        """Test that reusing the same gate across batches causes time drift."""
        
        # Create a single gate instance (like our production pattern)
        gate = RequestStartGate(delay=0.222)  # 4.5/sec limit
        
        print("\n🧪 Testing RequestStartGate reuse across multiple batches")
        
        # Simulate first batch (50 tracks)
        print("\n📦 Batch 1: Processing 50 tracks")
        batch1_start = time.time()
        
        batch1_tasks = [
            asyncio.create_task(gate.wait(f"batch1_{i}"))
            for i in range(50)
        ]
        await asyncio.gather(*batch1_tasks)
        
        batch1_duration = time.time() - batch1_start
        batch1_expected = 50 * 0.222  # ~11.1 seconds
        
        print(f"   Batch 1 completed in {batch1_duration:.3f}s (expected ~{batch1_expected:.1f}s)")
        
        # Check gate state after batch 1
        post_batch1_next_time = gate._next_request_time
        current_time = time.time()
        time_drift_after_batch1 = post_batch1_next_time - current_time
        
        print(f"   Gate state: _next_request_time is {time_drift_after_batch1:.3f}s ahead of current time")
        
        # Small gap between batches (like in production)
        print("\n⏳ Gap between batches (2 seconds)...")
        await asyncio.sleep(2.0)
        
        # Simulate second batch (49 tracks) - this is where the drift manifests
        print("\n📦 Batch 2: Processing 49 tracks")
        batch2_start = time.time()
        
        # Track first few wait times in batch 2
        batch2_wait_times = []
        
        async def timed_gate_wait(call_id: str) -> float:
            """Track individual gate wait times."""
            wait_start = time.time()
            await gate.wait(call_id)
            wait_time = time.time() - wait_start
            return wait_time
        
        # Just test first 5 calls of batch 2 to see the drift
        batch2_tasks = [
            asyncio.create_task(timed_gate_wait(f"batch2_{i}"))
            for i in range(5)
        ]
        
        batch2_wait_times = await asyncio.gather(*batch2_tasks)
        batch2_duration = time.time() - batch2_start
        
        print(f"   First 5 wait times in batch 2: {[round(w, 3) for w in batch2_wait_times]}s")
        print("   Expected pattern: [0.0, 0.222, 0.444, 0.666, 0.888]s")
        print(f"   Batch 2 (first 5) completed in {batch2_duration:.3f}s")
        
        # The smoking gun: first call of batch 2 should be immediate if gate resets properly
        first_wait_batch2 = batch2_wait_times[0]
        
        if first_wait_batch2 > 5.0:
            print(f"   🚨 DRIFT DETECTED: First call of batch 2 waited {first_wait_batch2:.3f}s!")
            print("   This matches the production issue pattern")
            print("   Gate did not reset between batches - same instance reused")
        else:
            print(f"   ✅ No drift: First call waited only {first_wait_batch2:.3f}s")
        
        # Verify the issue: time drift accumulation
        final_gate_state = gate._next_request_time
        current_time_final = time.time()
        final_drift = final_gate_state - current_time_final
        
        print("\n📊 Gate Reuse Analysis:")
        print("   Total batches: 2")
        print("   Gate instance: Same across both batches (production pattern)")
        print(f"   Final drift: {final_drift:.3f}s ahead")
        print(f"   First batch 2 wait: {first_wait_batch2:.3f}s")
        
        return {
            "batch1_duration": batch1_duration,
            "batch2_first_wait": first_wait_batch2,
            "time_drift_after_batch1": time_drift_after_batch1,
            "final_drift": final_drift,
            "drift_detected": first_wait_batch2 > 5.0,
            "matches_production_issue": first_wait_batch2 > 5.0,
        }

    @pytest.mark.asyncio
    async def test_gate_fresh_instance_no_drift(self):
        """Test that using fresh gate instances prevents drift (control test)."""
        
        print("\n🧪 Testing fresh RequestStartGate instances (control)")
        
        # Simulate first batch with fresh gate
        print("\n📦 Batch 1: Fresh gate instance")
        batch1_gate = RequestStartGate(delay=0.222)
        batch1_start = time.time()
        
        batch1_tasks = [
            asyncio.create_task(batch1_gate.wait(f"batch1_{i}"))
            for i in range(10)  # Smaller for test speed
        ]
        await asyncio.gather(*batch1_tasks)
        
        batch1_duration = time.time() - batch1_start
        print(f"   Batch 1 completed in {batch1_duration:.3f}s")
        
        # Gap between batches
        await asyncio.sleep(2.0)
        
        # Simulate second batch with NEW gate instance
        print("\n📦 Batch 2: Fresh gate instance (different from batch 1)")
        batch2_gate = RequestStartGate(delay=0.222)  # NEW INSTANCE!
        
        async def timed_gate_wait_fresh(call_id: str) -> float:
            """Track individual gate wait times with fresh gate."""
            wait_start = time.time()
            await batch2_gate.wait(call_id)  # Different gate!
            wait_time = time.time() - wait_start
            return wait_time
        
        batch2_tasks = [
            asyncio.create_task(timed_gate_wait_fresh(f"batch2_{i}"))
            for i in range(5)
        ]
        
        batch2_wait_times = await asyncio.gather(*batch2_tasks)
        first_wait_batch2 = batch2_wait_times[0]
        
        print(f"   First 5 wait times in batch 2: {[round(w, 3) for w in batch2_wait_times]}s")
        print("   Expected pattern: [0.0, 0.222, 0.444, 0.666, 0.888]s")
        
        if first_wait_batch2 < 0.1:
            print(f"   ✅ No drift with fresh instances: First call waited {first_wait_batch2:.3f}s")
        else:
            print(f"   🚨 Unexpected: Fresh instance had drift {first_wait_batch2:.3f}s")
        
        return {
            "batch1_duration": batch1_duration,
            "batch2_first_wait": first_wait_batch2,
            "fresh_instances_prevent_drift": first_wait_batch2 < 0.1,
        }