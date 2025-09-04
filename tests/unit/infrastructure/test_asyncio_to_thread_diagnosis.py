"""Test to diagnose if asyncio.to_thread itself is serializing."""

import asyncio
import threading
import time
from typing import Any

import pytest


class TestAsyncioToThreadDiagnosis:
    """Diagnose asyncio.to_thread behavior without any external libraries."""

    @pytest.mark.asyncio
    async def test_asyncio_to_thread_pure_concurrency(self):
        """Test asyncio.to_thread with pure Python functions (no external libs)."""
        
        def blocking_operation(call_id: int) -> dict[str, Any]:
            """Pure Python blocking operation - no HTTP, no external libs."""
            thread_id = threading.get_ident()
            start_time = time.time()
            
            print(f"   Call {call_id}: Started on thread {thread_id} at {start_time:.3f}")
            
            # Pure CPU/sleep work - no network, no shared resources
            time.sleep(1.0)  # Simulate 1 second work
            
            end_time = time.time()
            duration = end_time - start_time
            
            result = {
                "call_id": call_id,
                "thread_id": thread_id,
                "start_time": start_time,
                "end_time": end_time,
                "duration": duration
            }
            
            print(f"   Call {call_id}: Completed on thread {thread_id} after {duration:.3f}s")
            return result
        
        print("\n🧪 Testing asyncio.to_thread with pure Python functions")
        
        # Test 5 concurrent calls
        num_calls = 5
        overall_start = time.time()
        
        print(f"   Starting {num_calls} concurrent asyncio.to_thread calls...")
        
        # Create all tasks at once - same pattern as our Last.fm code
        tasks = [
            asyncio.create_task(asyncio.to_thread(blocking_operation, i))
            for i in range(num_calls)
        ]
        
        # Wait for all to complete - same as our asyncio.as_completed pattern
        results = await asyncio.gather(*tasks)
        overall_duration = time.time() - overall_start
        
        print("\n📊 Pure Python asyncio.to_thread Results:")
        print(f"   Total time: {overall_duration:.3f}s")
        print("   Expected concurrent: ~1.0s")
        print("   Expected sequential: ~5.0s")
        
        # Analyze results
        unique_threads = len({r["thread_id"] for r in results})
        
        # Check for overlapping execution
        overlaps = 0
        for i, r1 in enumerate(results):
            for j, r2 in enumerate(results):
                if i != j:
                    if r1["start_time"] < r2["end_time"] and r1["end_time"] > r2["start_time"]:
                        overlaps += 1
        
        concurrent_calls = overlaps // 2 + 1 if overlaps > 0 else 1
        
        print(f"   Unique threads: {unique_threads}")
        print(f"   Concurrent calls detected: {concurrent_calls}")
        
        # Check timing distribution
        start_times = [r["start_time"] for r in results]
        min_start = min(start_times)
        max_start = max(start_times)
        start_spread = max_start - min_start
        
        print(f"   Start time spread: {start_spread:.3f}s")
        
        if overall_duration < 2.0 and unique_threads > 1:
            print("   ✅ SUCCESS: asyncio.to_thread works correctly")
            print("   The bottleneck is NOT in asyncio.to_thread itself")
            asyncio_works = True
        elif overall_duration > 3.0:
            print("   🚨 PROBLEM: asyncio.to_thread is serializing!")
            print("   This would explain the Last.fm issue")
            asyncio_works = False
        else:
            print("   ⚠️ UNCLEAR: Partial concurrency detected")
            asyncio_works = None
        
        return {
            "overall_duration": overall_duration,
            "unique_threads": unique_threads,
            "concurrent_calls": concurrent_calls,
            "start_spread": start_spread,
            "asyncio_works": asyncio_works
        }

    @pytest.mark.asyncio
    async def test_requests_library_serialization(self):
        """Test if requests library itself serializes when used in threads."""
        
        def http_request_operation(call_id: int) -> dict[str, Any]:
            """Make HTTP request using requests library."""
            import threading

            import requests
            
            thread_id = threading.get_ident()
            start_time = time.time()
            
            print(f"   HTTP Call {call_id}: Started on thread {thread_id}")
            
            try:
                # Make request to a fast API (httpbin echo)
                response = requests.get("https://httpbin.org/delay/1", timeout=5)
                success = response.status_code == 200
            except Exception as e:
                success = False
                print(f"   HTTP Call {call_id}: Error {e}")
            
            end_time = time.time()
            duration = end_time - start_time
            
            print(f"   HTTP Call {call_id}: Completed in {duration:.3f}s (success: {success})")
            
            return {
                "call_id": call_id,
                "thread_id": thread_id,
                "duration": duration,
                "success": success
            }
        
        print("\n🌐 Testing HTTP requests library in asyncio.to_thread")
        
        num_calls = 3  # Fewer calls to avoid overwhelming httpbin
        overall_start = time.time()
        
        tasks = [
            asyncio.create_task(asyncio.to_thread(http_request_operation, i))
            for i in range(num_calls)
        ]
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        overall_duration = time.time() - overall_start
        
        # Filter out exceptions
        valid_results = [r for r in results if isinstance(r, dict)]
        
        print("\n📊 HTTP Requests Results:")
        print(f"   Total time: {overall_duration:.3f}s")
        print(f"   Valid results: {len(valid_results)}/{num_calls}")
        
        if valid_results:
            unique_threads = len({r["thread_id"] for r in valid_results})
            print(f"   Unique threads: {unique_threads}")
            
            if overall_duration < 2.0 and unique_threads > 1:
                print("   ✅ HTTP CONCURRENT: Requests library allows concurrency")
            else:
                print("   ⚠️ HTTP SERIALIZED: Requests might be serializing")
        
        return valid_results

    @pytest.mark.asyncio  
    async def test_thread_pool_capacity_verification(self):
        """Verify that our custom thread pool is actually being used."""
        
        def get_thread_pool_info() -> dict[str, Any]:
            """Get information about current thread pool."""
            import asyncio
            import threading
            
            thread_id = threading.get_ident()
            thread_name = threading.current_thread().name
            
            try:
                loop = asyncio.get_running_loop()
                executor = getattr(loop, '_default_executor', None)
                
                return {
                    "thread_id": thread_id,
                    "thread_name": thread_name,
                    "has_executor": executor is not None,
                    "executor_type": type(executor).__name__ if executor else None,
                    "max_workers": getattr(executor, '_max_workers', None) if executor else None,
                }
            except Exception as e:
                return {
                    "thread_id": thread_id,
                    "thread_name": thread_name,
                    "error": str(e)
                }
        
        print("\n🔧 Verifying thread pool configuration")
        
        # Test with multiple concurrent calls
        tasks = [
            asyncio.create_task(asyncio.to_thread(get_thread_pool_info))
            for _ in range(10)
        ]
        
        results = await asyncio.gather(*tasks)
        
        # Analyze thread pool usage
        unique_threads = len({r["thread_id"] for r in results})
        thread_names = {r.get("thread_name", "unknown") for r in results}
        executors = [r for r in results if r.get("has_executor")]
        
        print(f"   Threads used: {unique_threads}")
        print(f"   Thread names: {thread_names}")
        
        if executors:
            executor_info = executors[0]  # All should be the same
            print(f"   Executor type: {executor_info.get('executor_type')}")
            print(f"   Max workers: {executor_info.get('max_workers')}")
            
            if executor_info.get('max_workers') == 200:
                print("   ✅ Custom 200-worker thread pool is active")
            else:
                print("   ❌ Custom thread pool NOT active")
        else:
            print("   ❌ No executor information available")
        
        return results