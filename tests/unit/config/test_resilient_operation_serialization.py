"""Test to verify resilient_operation decorator doesn't cause serialization."""

import asyncio
import time

import pytest

from src.config.logging import resilient_operation


class TestResilientOperationConcurrency:
    """Test that resilient_operation decorator allows concurrent execution."""

    @pytest.mark.asyncio
    async def test_resilient_operation_allows_concurrency(self):
        """Test that multiple functions with @resilient_operation can run concurrently."""
        
        # Create a mock async function with artificial delay
        @resilient_operation("test_concurrent_operation")
        async def mock_async_operation(operation_id: int, delay: float = 0.5) -> dict:
            """Mock operation that simulates work."""
            start_time = time.time()
            
            # Simulate async work (network call, etc.)
            await asyncio.sleep(delay)
            
            end_time = time.time()
            return {
                "operation_id": operation_id,
                "delay": delay,
                "actual_duration": end_time - start_time,
                "start_time": start_time,
                "end_time": end_time
            }
        
        print("\n🧪 Testing resilient_operation decorator concurrency")
        
        # Test concurrent execution of 5 operations
        num_operations = 5
        operation_delay = 0.5  # Half second each
        
        overall_start = time.time()
        
        # Create all tasks concurrently
        tasks = [
            asyncio.create_task(mock_async_operation(i, operation_delay))
            for i in range(num_operations)
        ]
        
        # Wait for all to complete
        results = await asyncio.gather(*tasks)
        
        overall_end = time.time()
        total_duration = overall_end - overall_start
        
        print("\n📊 Resilient Operation Concurrency Results:")
        print(f"   Operations: {num_operations}")
        print(f"   Individual delay: {operation_delay}s each")
        print(f"   Total time: {total_duration:.3f}s")
        print(f"   Expected concurrent: ~{operation_delay}s")
        print(f"   Expected sequential: ~{num_operations * operation_delay}s")
        
        # Verify concurrent execution
        assert total_duration < (operation_delay + 0.1), f"Expected ~{operation_delay}s, got {total_duration:.3f}s - indicates serialization!"
        
        # Verify all operations completed
        assert len(results) == num_operations, f"Expected {num_operations} results, got {len(results)}"
        
        # Check for overlapping execution
        start_times = [r["start_time"] for r in results]
        [r["end_time"] for r in results]
        
        min_start = min(start_times)
        max_start = max(start_times)
        start_spread = max_start - min_start
        
        print(f"   Start time spread: {start_spread:.3f}s")
        print(f"   All operations started within: {start_spread:.3f}s")
        
        # All operations should start nearly simultaneously
        assert start_spread < 0.1, f"Operations should start concurrently, got {start_spread:.3f}s spread"
        
        print("\n✅ resilient_operation decorator allows concurrent execution")
        
        return {
            "total_duration": total_duration,
            "start_spread": start_spread,
            "concurrent": total_duration < 1.0,
            "results": len(results)
        }

    @pytest.mark.asyncio
    async def test_resilient_operation_with_asyncio_to_thread(self):
        """Test resilient_operation with asyncio.to_thread (closer to our LastFM pattern)."""
        
        def blocking_work(operation_id: int, delay: float = 0.3) -> dict:
            """Simulate blocking work (like HTTP call)."""
            import threading
            import time
            
            thread_id = threading.get_ident()
            thread_name = threading.current_thread().name
            start_time = time.time()
            
            # Simulate blocking network call
            time.sleep(delay)
            
            end_time = time.time()
            return {
                "operation_id": operation_id,
                "thread_id": thread_id,
                "thread_name": thread_name,
                "delay": delay,
                "actual_duration": end_time - start_time,
            }
        
        @resilient_operation("test_to_thread_operation") 
        async def async_operation_with_thread(operation_id: int) -> dict:
            """Async operation that uses asyncio.to_thread (like our LastFM calls)."""
            # This mirrors our LastFM client pattern
            result = await asyncio.to_thread(blocking_work, operation_id, 0.3)
            return result
        
        print("\n🧪 Testing resilient_operation with asyncio.to_thread")
        
        num_operations = 5
        overall_start = time.time()
        
        # Create concurrent tasks (same as our APIBatchProcessor)
        tasks = [
            asyncio.create_task(async_operation_with_thread(i))
            for i in range(num_operations)
        ]
        
        results = await asyncio.gather(*tasks)
        total_duration = time.time() - overall_start
        
        print("\n📊 Resilient Operation + asyncio.to_thread Results:")
        print(f"   Operations: {num_operations}")
        print(f"   Total time: {total_duration:.3f}s")
        print("   Expected concurrent: ~0.3s") 
        print("   Expected sequential: ~1.5s")
        
        # Check thread usage
        thread_ids = [r["thread_id"] for r in results]
        unique_threads = len(set(thread_ids))
        thread_names = {r["thread_name"] for r in results}
        
        print(f"   Unique threads: {unique_threads}")
        print(f"   Thread names: {thread_names}")
        
        # Verify concurrent execution (should complete in ~0.3s, not ~1.5s)
        assert total_duration < 0.6, f"Expected concurrent execution (~0.3s), got {total_duration:.3f}s"
        assert unique_threads > 1, f"Expected multiple threads, got {unique_threads}"
        
        print("\n✅ resilient_operation + asyncio.to_thread works concurrently")
        
        return {
            "total_duration": total_duration,
            "unique_threads": unique_threads,
            "thread_names": list(thread_names),
            "concurrent": total_duration < 0.6,
        }

    @pytest.mark.asyncio 
    async def test_mock_lastfm_pattern_with_decorator(self):
        """Test the exact pattern used in our LastFM client."""
        
        def mock_pylast_call(artist: str, title: str) -> dict:
            """Mock the pylast.get_track() call."""
            import threading
            import time
            
            thread_id = threading.get_ident()
            start_time = time.time()
            
            # Simulate HTTP request (similar to pylast)
            time.sleep(0.2)  # 200ms network call
            
            return {
                "artist": artist,
                "title": title, 
                "thread_id": thread_id,
                "duration": time.time() - start_time,
                "found": True,
            }
        
        @resilient_operation("lastfm_get_track") 
        async def mock_lastfm_get_track(artist: str, title: str) -> dict:
            """Mock LastFM get_track method with identical decorator."""
            # This exactly mirrors our current LastFM client implementation
            result = await asyncio.to_thread(mock_pylast_call, artist, title)
            return result
        
        # Test data that mirrors our real usage
        test_tracks = [
            ("Artist1", "Track1"),
            ("Artist2", "Track2"), 
            ("Artist3", "Track3"),
            ("Artist4", "Track4"),
            ("Artist5", "Track5"),
        ]
        
        print("\n🧪 Testing mock LastFM pattern with resilient_operation")
        
        overall_start = time.time()
        
        # Create tasks exactly like APIBatchProcessor
        tasks = [
            asyncio.create_task(mock_lastfm_get_track(artist, title))
            for artist, title in test_tracks
        ]
        
        results = await asyncio.gather(*tasks)
        total_duration = time.time() - overall_start
        
        print("\n📊 Mock LastFM Pattern Results:")
        print(f"   Tracks: {len(test_tracks)}")
        print(f"   Total time: {total_duration:.3f}s") 
        print("   Expected concurrent: ~0.2s")
        print("   Expected sequential: ~1.0s")
        
        unique_threads = len({r["thread_id"] for r in results})
        print(f"   Unique threads: {unique_threads}")
        
        # This should prove definitively if resilient_operation is the issue
        if total_duration > 0.5:
            print("   🚨 SERIALIZATION DETECTED: resilient_operation may be the culprit!")
        else:
            print("   ✅ CONCURRENT EXECUTION: resilient_operation is NOT the issue")
        
        assert total_duration < 0.5, f"Expected concurrent execution, got {total_duration:.3f}s"
        assert unique_threads > 1, f"Expected multiple threads, got {unique_threads}"
        
        return {
            "total_duration": total_duration,
            "unique_threads": unique_threads,
            "concurrent": total_duration < 0.5,
            "serialization_detected": total_duration > 0.5,
        }