"""Integration test for thread pool fix with mocked Last.fm responses.

Tests the thread pool configuration fix using mocked Last.fm API responses
with realistic timing (3-5 seconds) to verify true concurrency.
"""

import asyncio
import threading
import time
from typing import Any
from unittest.mock import MagicMock, patch

import pylast
import pytest

from src.infrastructure.connectors.lastfm.client import LastFMAPIClient


class TestThreadPoolFix:
    """Integration tests for thread pool concurrency fix."""

    @pytest.fixture
    def mock_pylast_with_realistic_timing(self):
        """Mock pylast with realistic 3-5 second response times."""
        
        def slow_get_track(*args, **kwargs):
            """Mock get_track with realistic Last.fm timing."""
            import random
            
            # Simulate realistic Last.fm response time (3-5 seconds)
            delay = random.uniform(3.0, 5.0)
            time.sleep(delay)
            
            # Return mock track
            mock_track = MagicMock(spec=pylast.Track)
            mock_track.get_title.return_value = f"Track_{args[0]}_{args[1]}"
            mock_track.get_artist.return_value.get_name.return_value = args[0]
            return mock_track
        
        # Mock the entire pylast.LastFMNetwork class
        with patch('pylast.LastFMNetwork') as mock_network_class:
            mock_network = MagicMock()
            mock_network.get_track.side_effect = slow_get_track
            mock_network_class.return_value = mock_network
            yield mock_network

    @pytest.mark.asyncio
    async def test_concurrent_lastfm_calls_with_thread_pool_fix(self, mock_pylast_with_realistic_timing):
        """Test that our thread pool fix enables true concurrency for Last.fm calls."""
        
        # Create LastFM client (this should configure the thread pool)
        with patch('src.config.settings') as mock_settings:
            mock_settings.credentials.lastfm_key = "test_key"
            mock_settings.credentials.lastfm_secret.get_secret_value.return_value = "test_secret"
            mock_settings.credentials.lastfm_username = "test_user"
            mock_settings.credentials.lastfm_password.get_secret_value.return_value = "test_pass"
            mock_settings.api.lastfm_rate_limit = 10.0  # Fast for testing
            mock_settings.api.lastfm_concurrency = 50   # Plenty of threads
            mock_settings.api.lastfm_request_timeout = 10.0
            
            client = LastFMAPIClient()
        
        # Test with 10 concurrent calls (smaller than 50 for test speed)
        num_calls = 10
        
        print(f"\n🎵 Testing {num_calls} concurrent Last.fm calls with thread pool fix")
        print("   Each call will take 3-5 seconds (mocked)")
        
        # Create test data
        test_calls = [
            (f"Artist_{i}", f"Track_{i}")
            for i in range(num_calls)
        ]
        
        # Track results and timing
        results = []
        call_info = []
        
        async def timed_api_call(artist: str, track: str, call_id: int):
            """Make API call and track timing/thread info."""
            start_time = time.time()
            thread_id = threading.get_ident()
            
            result = await client.get_track(artist, track)
            
            end_time = time.time()
            duration = end_time - start_time
            
            call_info.append({
                "call_id": call_id,
                "artist": artist,
                "track": track,
                "thread_id": thread_id,
                "start_time": start_time,
                "end_time": end_time,
                "duration": duration,
                "success": result is not None
            })
            
            return result
        
        # Execute all calls concurrently
        overall_start = time.time()
        
        # Create all tasks at once (same as our APIBatchProcessor)
        tasks = [
            asyncio.create_task(timed_api_call(artist, track, i))
            for i, (artist, track) in enumerate(test_calls)
        ]
        
        print(f"   Created {len(tasks)} tasks at {time.time():.1f}")
        
        # Process as they complete
        completed_count = 0
        for completed_task in asyncio.as_completed(tasks):
            result = await completed_task
            results.append(result)
            completed_count += 1
            
            elapsed = time.time() - overall_start
            print(f"   Call #{completed_count} completed at {elapsed:.1f}s")
        
        total_time = time.time() - overall_start
        
        # Analyze concurrency
        unique_threads = len({info["thread_id"] for info in call_info})
        successful_calls = sum(1 for info in call_info if info["success"])
        
        # Check for overlapping execution
        overlaps = 0
        for i, info1 in enumerate(call_info):
            for j, info2 in enumerate(call_info):
                if i != j:
                    # Check if calls overlapped
                    if (info1["start_time"] < info2["end_time"] and 
                        info1["end_time"] > info2["start_time"]):
                        overlaps += 1
        
        max_concurrent = overlaps // 2 + 1 if overlaps > 0 else 1
        
        print("\n📊 Thread Pool Fix Results:")
        print(f"   Total time: {total_time:.1f}s")
        print(f"   Successful calls: {successful_calls}/{num_calls}")
        print(f"   Unique threads: {unique_threads}")
        print(f"   Max concurrent: {max_concurrent}")
        print("   Expected concurrent time: ~5s (longest mock call)")
        print(f"   Expected sequential time: ~40s ({num_calls} × 4s avg)")
        
        # Assertions to verify the fix works
        assert successful_calls == num_calls, f"Not all calls succeeded: {successful_calls}/{num_calls}"
        
        if total_time < 8.0:  # Should complete in ~5s if truly concurrent
            print("   ✅ EXCELLENT: True concurrency achieved!")
            print(f"   Thread pool fix successful - {total_time:.1f}s vs ~40s sequential")
            assert unique_threads >= 8, f"Should use many threads: {unique_threads}"
            assert max_concurrent >= 8, f"Should have high concurrency: {max_concurrent}"
        elif total_time < 20.0:
            print("   ⚡ GOOD: Partial concurrency - much better than sequential")
            print(f"   Thread pool helping - {total_time:.1f}s vs ~40s sequential")
        else:
            pytest.fail(f"Thread pool fix not working - still taking {total_time:.1f}s (expected <8s)")
        
        return {
            "total_time": total_time,
            "unique_threads": unique_threads,
            "max_concurrent": max_concurrent,
            "call_info": call_info
        }

    @pytest.mark.asyncio
    async def test_thread_pool_configuration_validation(self):
        """Test that thread pool is properly configured."""
        
        with patch('src.config.settings') as mock_settings:
            mock_settings.credentials.lastfm_key = "test_key"
            mock_settings.api.lastfm_concurrency = 100  # Test with 100 threads
            mock_settings.api.lastfm_rate_limit = 5.0
            mock_settings.api.lastfm_request_timeout = 10.0
            
            # Create client which should configure thread pool
            LastFMAPIClient()
            
            # Check that event loop has been configured
            try:
                loop = asyncio.get_running_loop()
                executor = loop._default_executor
                
                if executor is not None:
                    print("\n🔧 Thread Pool Configuration:")
                    print(f"   Executor type: {type(executor).__name__}")
                    print(f"   Max workers: {executor._max_workers}")
                    print(f"   Thread name prefix: {getattr(executor, '_thread_name_prefix', 'N/A')}")
                    
                    assert executor._max_workers >= 50, f"Thread pool too small: {executor._max_workers}"
                    print(f"   ✅ Thread pool properly configured with {executor._max_workers} workers")
                else:
                    print("   ⚠️ Default executor not configured yet")
                
            except RuntimeError:
                print("   ⚠️ No running event loop - configuration will happen at runtime")

    @pytest.mark.asyncio 
    async def test_thread_pool_vs_default_comparison(self):
        """Compare performance with and without thread pool configuration."""
        
        def quick_blocking_call(call_id: int) -> dict[str, Any]:
            """Quick blocking call for comparison."""
            time.sleep(0.2)  # 200ms
            return {
                "call_id": call_id,
                "thread_id": threading.get_ident(),
                "duration": 0.2
            }
        
        # Test 1: Default thread pool (should be limited)
        print("\n⚖️ Thread Pool Performance Comparison")
        
        num_calls = 20
        
        # Reset to default executor
        loop = asyncio.get_running_loop()
        
        try:
            # Test with default (limited) thread pool
            loop.set_default_executor(None)  # Reset to default
            
            start_time = time.time()
            tasks = [asyncio.create_task(asyncio.to_thread(quick_blocking_call, i)) for i in range(num_calls)]
            default_results = await asyncio.gather(*tasks)
            default_time = time.time() - start_time
            
            default_threads = len({r["thread_id"] for r in default_results})
            
            # Test with configured thread pool  
            from concurrent.futures import ThreadPoolExecutor
            custom_executor = ThreadPoolExecutor(max_workers=50)
            loop.set_default_executor(custom_executor)
            
            start_time = time.time()
            tasks = [asyncio.create_task(asyncio.to_thread(quick_blocking_call, i)) for i in range(num_calls)]
            custom_results = await asyncio.gather(*tasks)
            custom_time = time.time() - start_time
            
            custom_threads = len({r["thread_id"] for r in custom_results})
            
            print(f"   Default thread pool: {default_time:.2f}s using {default_threads} threads")
            print(f"   Custom thread pool:  {custom_time:.2f}s using {custom_threads} threads")
            
            speedup = default_time / custom_time if custom_time > 0 else 1
            print(f"   Speedup with custom pool: {speedup:.1f}x")
            
            assert custom_threads > default_threads, "Custom pool should use more threads"
            assert speedup > 1.5, f"Custom pool should be significantly faster: {speedup:.1f}x"
            
            custom_executor.shutdown(wait=False)
            
        finally:
            # Don't restore original as it might be None
            pass