"""Test RequestStartGate with realistic production timing patterns."""

import asyncio
import time

import pytest

from src.infrastructure.connectors._shared.request_start_gate import RequestStartGate


class TestRequestStartGateRealisticProduction:
    """Test RequestStartGate with production-like timing patterns."""

    @pytest.mark.asyncio
    async def test_gate_with_slow_http_calls_like_production(self):
        """Test gate behavior when HTTP calls take 3-5 seconds each (like production)."""
        
        gate = RequestStartGate(delay=0.222)  # 4.5/sec limit
        
        print("\n🧪 Testing RequestStartGate with realistic 3-5s HTTP call timings")
        
        async def realistic_slow_api_call(call_id: int) -> dict:
            """Simulate realistic LastFM API call with 3-5s HTTP time."""
            # Wait for gate approval first
            gate_start = time.time()
            await gate.wait(f"realistic_{call_id}")
            gate_wait = time.time() - gate_start
            
            # Simulate actual HTTP call duration (like pylast network call)
            http_start = time.time()
            await asyncio.sleep(3.5)  # 3.5s HTTP call like production
            http_duration = time.time() - http_start
            
            total_time = time.time() - gate_start
            
            return {
                "call_id": call_id,
                "gate_wait_time": gate_wait,
                "http_duration": http_duration, 
                "total_call_time": total_time,
            }
        
        # Process 10 items like a batch (smaller for test speed)
        print("\n📦 Processing 10 API calls with 3.5s HTTP time each")
        batch_start = time.time()
        
        tasks = [
            asyncio.create_task(realistic_slow_api_call(i))
            for i in range(10)
        ]
        
        results = await asyncio.gather(*tasks)
        batch_duration = time.time() - batch_start
        
        gate_wait_times = [r["gate_wait_time"] for r in results]
        http_times = [r["http_duration"] for r in results]
        [r["total_call_time"] for r in results]
        
        print("\n📊 Realistic Production Pattern Results:")
        print(f"   Batch size: {len(results)}")
        print(f"   Total batch time: {batch_duration:.3f}s")
        print(f"   Gate wait times: {[round(w, 3) for w in gate_wait_times]}s")
        print(f"   HTTP durations: {[round(h, 3) for h in http_times]}s")
        print("   Expected gate pattern: [0.0, 0.222, 0.444, 0.666, ...]s")
        
        # The key insight: gate waits should still be well-behaved even with slow HTTP
        max_gate_wait = max(gate_wait_times)
        expected_max_gate_wait = 10 * 0.222  # 2.22s for 10 items
        
        if max_gate_wait > expected_max_gate_wait + 1.0:  # Allow 1s tolerance
            print(f"   🚨 EXCESSIVE GATE WAIT: Max wait {max_gate_wait:.3f}s vs expected {expected_max_gate_wait:.3f}s")
        else:
            print(f"   ✅ Gate waits are reasonable: Max {max_gate_wait:.3f}s")
        
        # Check for proper staggered timing
        properly_staggered = all(
            0.15 <= gate_wait_times[i + 1] - gate_wait_times[i] <= 0.35
            for i in range(len(gate_wait_times) - 1) if gate_wait_times[i + 1] > gate_wait_times[i]
        )
        
        if properly_staggered:
            print("   ✅ Proper 222ms staggering maintained")
        else:
            print("   🚨 Staggering disrupted")
        
        return {
            "batch_duration": batch_duration,
            "gate_wait_times": gate_wait_times,
            "max_gate_wait": max_gate_wait,
            "expected_max": expected_max_gate_wait,
            "excessive_waits": max_gate_wait > expected_max_gate_wait + 1.0,
        }

    @pytest.mark.asyncio
    async def test_gate_across_multiple_realistic_batches(self):
        """Test gate behavior across multiple batches with realistic HTTP timing."""
        
        gate = RequestStartGate(delay=0.222)  # Same gate instance across batches
        
        print("\n🧪 Testing RequestStartGate across multiple realistic batches")
        
        async def realistic_batch_api_call(batch_id: int, call_id: int) -> dict:
            """Simulate realistic API call within a batch."""
            gate_start = time.time()
            await gate.wait(f"batch{batch_id}_call{call_id}")
            gate_wait = time.time() - gate_start
            
            # Simulate HTTP call
            await asyncio.sleep(2.0)  # 2s HTTP for faster test
            
            return {
                "batch_id": batch_id,
                "call_id": call_id,
                "gate_wait_time": gate_wait,
            }
        
        batch_results = []
        
        # Process 3 batches sequentially (like production workflow)
        for batch_num in range(3):
            print(f"\n📦 Batch {batch_num + 1}: Processing 5 items")
            batch_start = time.time()
            
            # Check gate state before batch
            pre_batch_next_time = gate._next_request_time
            current_time = time.time()
            pre_batch_drift = pre_batch_next_time - current_time if pre_batch_next_time > 0 else 0
            
            print(f"   Pre-batch gate drift: {pre_batch_drift:.3f}s ahead")
            
            tasks = [
                asyncio.create_task(realistic_batch_api_call(batch_num, i))
                for i in range(5)
            ]
            
            results = await asyncio.gather(*tasks)
            batch_duration = time.time() - batch_start
            
            gate_waits = [r["gate_wait_time"] for r in results]
            first_wait = gate_waits[0]
            
            print(f"   Batch completed in {batch_duration:.3f}s")
            print(f"   Gate waits: {[round(w, 3) for w in gate_waits]}s")
            print(f"   First call wait: {first_wait:.3f}s")
            
            batch_results.append({
                "batch_num": batch_num,
                "batch_duration": batch_duration,
                "gate_waits": gate_waits,
                "first_wait": first_wait,
                "pre_batch_drift": pre_batch_drift,
            })
            
            # Small gap between batches (like production)
            if batch_num < 2:
                print("   Gap before next batch...")
                await asyncio.sleep(1.0)
        
        print("\n📊 Multi-Batch Analysis:")
        
        # Look for drift accumulation
        first_waits = [b["first_wait"] for b in batch_results]
        pre_batch_drifts = [b["pre_batch_drift"] for b in batch_results]
        
        print(f"   First wait per batch: {[round(w, 3) for w in first_waits]}s")
        print(f"   Pre-batch drift: {[round(d, 3) for d in pre_batch_drifts]}s")
        
        # The smoking gun would be if later batches have massive first waits
        problem_detected = False
        for i, batch in enumerate(batch_results):
            if batch["first_wait"] > 5.0:  # Excessive delay like production
                print(f"   🚨 BATCH {i + 1} ISSUE: First wait {batch['first_wait']:.3f}s (should be ~0.0s)")
                problem_detected = True
        
        if not problem_detected:
            print("   ✅ All batches started promptly")
        
        return {
            "batch_results": batch_results,
            "first_waits": first_waits,
            "pre_batch_drifts": pre_batch_drifts,
            "problem_detected": problem_detected,
        }

    @pytest.mark.asyncio  
    async def test_gate_with_batch_processing_failure_recovery(self):
        """Test gate behavior when some batch operations fail or timeout."""
        
        gate = RequestStartGate(delay=0.222)
        
        print("\n🧪 Testing RequestStartGate with failure/timeout scenarios")
        
        async def api_call_with_failures(call_id: int) -> dict:
            """Simulate API call that might fail or timeout."""
            gate_start = time.time()
            await gate.wait(f"failure_test_{call_id}")
            gate_wait = time.time() - gate_start
            
            # Simulate various failure scenarios
            if call_id == 2:
                # Simulate timeout/cancellation
                raise TimeoutError("Simulated timeout")
            elif call_id == 4: 
                # Simulate API error
                raise Exception("Simulated API error")
            else:
                # Normal successful call
                await asyncio.sleep(1.0)
            
            return {
                "call_id": call_id,
                "gate_wait_time": gate_wait,
                "success": True,
            }
        
        # Process batch with failures
        print("\n📦 Processing batch with simulated failures")
        
        tasks = [
            asyncio.create_task(api_call_with_failures(i))
            for i in range(8)
        ]
        
        # Gather with return_exceptions to handle failures gracefully
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        successful_results = []
        failed_count = 0
        
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                print(f"   Call {i} failed: {type(result).__name__}")
                failed_count += 1
            else:
                successful_results.append(result)
        
        if successful_results:
            gate_waits = [r["gate_wait_time"] for r in successful_results]
            print(f"   Successful calls: {len(successful_results)}")
            print(f"   Failed calls: {failed_count}")
            print(f"   Gate waits for successful: {[round(w, 3) for w in gate_waits]}s")
            
            # Check if failures disrupted gate timing
            proper_timing = all(w < 2.0 for w in gate_waits)  # No excessive waits
            
            if proper_timing:
                print("   ✅ Gate timing unaffected by failures")
            else:
                print("   🚨 Gate timing disrupted by failures")
        
        # Test recovery with new batch after failures
        print("\n📦 Recovery batch after failures")
        
        async def recovery_api_call(call_id: int) -> dict:
            """Normal API call for recovery test."""
            gate_start = time.time()
            await gate.wait(f"recovery_{call_id}")
            gate_wait = time.time() - gate_start
            await asyncio.sleep(0.5)
            
            return {"call_id": call_id, "gate_wait_time": gate_wait}
        
        recovery_tasks = [
            asyncio.create_task(recovery_api_call(i))
            for i in range(3)
        ]
        
        recovery_results = await asyncio.gather(*recovery_tasks)
        recovery_waits = [r["gate_wait_time"] for r in recovery_results]
        first_recovery_wait = recovery_waits[0]
        
        print(f"   Recovery gate waits: {[round(w, 3) for w in recovery_waits]}s")
        print(f"   First recovery wait: {first_recovery_wait:.3f}s")
        
        recovery_ok = first_recovery_wait < 1.0  # Should start promptly
        
        if recovery_ok:
            print("   ✅ Gate recovered properly after failures")
        else:
            print(f"   🚨 Gate did not recover: {first_recovery_wait:.3f}s delay")
        
        return {
            "successful_calls": len(successful_results),
            "failed_calls": failed_count,
            "recovery_waits": recovery_waits,
            "first_recovery_wait": first_recovery_wait,
            "recovery_ok": recovery_ok,
        }