"""Test to reproduce RequestStartGate time drift issue."""

import asyncio
import time

import pytest

from src.infrastructure.connectors._shared.request_start_gate import RequestStartGate


class TestRequestStartGateDrift:
    """Test RequestStartGate for time drift issues that cause massive delays."""

    @pytest.mark.asyncio
    async def test_sequential_vs_concurrent_gate_access(self):
        """Test gate behavior when calls arrive sequentially vs concurrently."""
        
        gate = RequestStartGate(delay=0.222)  # Same as production (4.5/sec)
        
        print("\n🧪 Testing RequestStartGate sequential vs concurrent access")
        
        # Test 1: Concurrent access (ideal case)
        print("\n📊 Test 1: Concurrent Gate Access")
        concurrent_start = time.time()
        
        async def concurrent_gate_call(call_id: int) -> dict:
            """Call gate concurrently."""
            start = time.time()
            await gate.wait(f"concurrent_{call_id}")
            end = time.time()
            return {
                "call_id": call_id,
                "wait_time": end - start,
                "completion_time": end
            }
        
        # All 5 calls at the same time (like our batch processor creates tasks)
        tasks = [
            asyncio.create_task(concurrent_gate_call(i))
            for i in range(5)
        ]
        
        concurrent_results = await asyncio.gather(*tasks)
        concurrent_duration = time.time() - concurrent_start
        
        wait_times = [r["wait_time"] for r in concurrent_results]
        print(f"   Total time: {concurrent_duration:.3f}s")
        print(f"   Individual wait times: {[round(w, 3) for w in wait_times]}s")
        print("   Expected pattern: [0.0, 0.2, 0.4, 0.6, 0.8]s")
        
        # Reset gate for next test
        gate = RequestStartGate(delay=0.222)
        
        # Test 2: Sequential access (what might be happening in production)
        print("\n📊 Test 2: Sequential Gate Access")
        sequential_start = time.time()
        
        async def sequential_gate_call(call_id: int) -> dict:
            """Call gate sequentially with some processing time."""
            # Simulate some work BEFORE calling gate (like our batch processor might do)
            await asyncio.sleep(0.1)  # 100ms of "setup" work
            
            gate_start = time.time()
            await gate.wait(f"sequential_{call_id}")
            gate_end = time.time()
            
            # Simulate some work AFTER gate approval (like HTTP call)
            await asyncio.sleep(0.2)  # 200ms of "HTTP" work
            
            return {
                "call_id": call_id,
                "gate_wait_time": gate_end - gate_start,
                "total_time": time.time() - sequential_start,
                "gate_completion_time": gate_end
            }
        
        sequential_results = []
        for i in range(5):
            result = await sequential_gate_call(i)
            sequential_results.append(result)
        
        sequential_duration = time.time() - sequential_start
        seq_wait_times = [r["gate_wait_time"] for r in sequential_results]
        
        print(f"   Total time: {sequential_duration:.3f}s")
        print(f"   Gate wait times: {[round(w, 3) for w in seq_wait_times]}s")
        print("   Expected gate waits: [0.0, 0.2, 0.4, 0.6, 0.8]s")
        
        # Analysis
        if max(seq_wait_times) > 2.0:
            print("   🚨 DRIFT DETECTED: Sequential access causes time drift!")
        else:
            print("   ✅ No drift: Sequential access works correctly")
        
        return {
            "concurrent_duration": concurrent_duration,
            "sequential_duration": sequential_duration,
            "concurrent_waits": wait_times,
            "sequential_waits": seq_wait_times,
            "drift_detected": max(seq_wait_times) > 2.0,
        }

    @pytest.mark.asyncio
    async def test_gate_with_realistic_batch_timing(self):
        """Test gate with timing pattern similar to our actual batch processing."""
        
        gate = RequestStartGate(delay=0.222)  # 4.5/sec limit
        
        print("\n🧪 Testing RequestStartGate with realistic batch timing")
        
        async def realistic_batch_item_processing(item_id: int) -> dict:
            """Simulate realistic batch item processing."""
            
            # Simulate the exact pattern from our batch processor:
            # 1. Task creation happens quickly
            # 2. But actual execution might be staggered due to asyncio scheduling
            
            processing_start = time.time()
            
            # Small delay to simulate asyncio task scheduling
            await asyncio.sleep(0.001 * item_id)  # Staggered by 1ms each
            
            # Now call the gate (like our LastFM client does)
            gate_start = time.time()
            await gate.wait(f"batch_item_{item_id}")
            gate_end = time.time()
            gate_wait_time = gate_end - gate_start
            
            # Simulate HTTP work (like our pylast call)
            await asyncio.sleep(0.1)  # 100ms HTTP call
            
            total_time = time.time() - processing_start
            
            return {
                "item_id": item_id,
                "gate_wait_time": gate_wait_time,
                "total_processing_time": total_time,
                "gate_start": gate_start,
                "gate_end": gate_end,
            }
        
        # Create tasks all at once (like APIBatchProcessor)
        batch_start = time.time()
        tasks = [
            asyncio.create_task(realistic_batch_item_processing(i))
            for i in range(10)  # 10 items in batch
        ]
        
        # Wait for completion using asyncio.as_completed (like APIBatchProcessor)
        results = []
        for completed_task in asyncio.as_completed(tasks):
            result = await completed_task
            results.append(result)
            print(f"   Item {result['item_id']}: gate_wait={result['gate_wait_time']:.3f}s, total={result['total_processing_time']:.3f}s")
        
        batch_duration = time.time() - batch_start
        gate_wait_times = [r["gate_wait_time"] for r in results]
        max_gate_wait = max(gate_wait_times)
        
        print("\n📊 Realistic Batch Processing Results:")
        print(f"   Batch size: {len(results)}")
        print(f"   Total batch time: {batch_duration:.3f}s")
        print(f"   Gate wait times: {[round(w, 3) for w in gate_wait_times]}s")
        print(f"   Max gate wait: {max_gate_wait:.3f}s")
        print("   Expected max wait: ~2.0s (10 items * 0.222s)")
        
        if max_gate_wait > 3.0:
            print("   🚨 EXCESSIVE DELAY: Gate wait times are too high!")
            print("   This matches the production issue pattern")
        else:
            print("   ✅ Gate working correctly")
        
        return {
            "batch_duration": batch_duration,
            "gate_wait_times": gate_wait_times,
            "max_gate_wait": max_gate_wait,
            "excessive_delay": max_gate_wait > 3.0,
        }

    @pytest.mark.asyncio
    async def test_gate_reset_behavior(self):
        """Test if RequestStartGate needs reset logic to prevent drift."""
        
        gate = RequestStartGate(delay=0.222)
        
        print("\n🧪 Testing RequestStartGate reset behavior")
        
        # Simulate a scenario where calls come in bursts with gaps
        
        # Burst 1: 3 concurrent calls
        print("   Burst 1: 3 concurrent calls")
        burst1_start = time.time()
        tasks1 = [
            asyncio.create_task(gate.wait(f"burst1_{i}"))
            for i in range(3)
        ]
        await asyncio.gather(*tasks1)
        burst1_duration = time.time() - burst1_start
        print(f"   Burst 1 completed in {burst1_duration:.3f}s")
        
        # Gap: No calls for 2 seconds (simulates batch boundary)
        print("   Waiting 2 seconds (simulating batch boundary)...")
        await asyncio.sleep(2.0)
        
        # Burst 2: 3 more calls
        print("   Burst 2: 3 concurrent calls after gap")
        burst2_start = time.time()
        
        burst2_waits = []
        for i in range(3):
            wait_start = time.time()
            await gate.wait(f"burst2_{i}")
            wait_end = time.time()
            burst2_waits.append(wait_end - wait_start)
        
        burst2_duration = time.time() - burst2_start
        
        print(f"   Burst 2 wait times: {[round(w, 3) for w in burst2_waits]}s")
        print(f"   Burst 2 completed in {burst2_duration:.3f}s")
        
        # The first call of burst 2 should be immediate if gate logic is correct
        first_wait_after_gap = burst2_waits[0]
        
        if first_wait_after_gap > 0.1:
            print(f"   🚨 DRIFT ISSUE: First call after gap waited {first_wait_after_gap:.3f}s")
            print("   Gate didn't reset properly after the gap")
        else:
            print("   ✅ Gate reset correctly after gap")
        
        return {
            "burst1_duration": burst1_duration,
            "burst2_duration": burst2_duration,
            "burst2_wait_times": burst2_waits,
            "first_wait_after_gap": first_wait_after_gap,
            "drift_after_gap": first_wait_after_gap > 0.1,
        }