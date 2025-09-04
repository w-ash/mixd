"""Test for pylast.LastFMNetwork instance sharing bottleneck.

This test verifies if sharing a single pylast.LastFMNetwork instance across
multiple concurrent threads causes HTTP-level serialization.
"""

import asyncio
import threading
import time
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.infrastructure.connectors.lastfm.client import LastFMAPIClient


class TestPylastInstanceBottleneck:
    """Test pylast instance sharing and HTTP serialization."""

    @pytest.fixture
    def mock_pylast_slow_response(self):
        """Mock pylast with realistic slow responses to test concurrency."""
        
        def slow_get_track(*args, **kwargs):
            """Mock get_track that simulates real HTTP delay."""
            # Each call takes 1 second to simulate HTTP request
            time.sleep(1.0)
            
            # Track which thread made the call
            thread_id = threading.get_ident()
            call_time = time.time()
            
            mock_track = MagicMock()
            mock_track.get_title.return_value = f"Track_{thread_id}_{call_time}"
            return mock_track
        
        # Mock the entire pylast.LastFMNetwork class
        with patch('pylast.LastFMNetwork') as mock_network_class:
            mock_network = MagicMock()
            mock_network.get_track.side_effect = slow_get_track
            mock_network_class.return_value = mock_network
            yield mock_network

    @pytest.mark.asyncio
    async def test_single_pylast_instance_serialization(self, mock_pylast_slow_response):
        """Test if single pylast instance causes serialization."""
        
        # Create single LastFM client (uses one pylast instance)
        with patch('src.config.settings') as mock_settings:
            mock_settings.credentials.lastfm_key = "test_key"
            mock_settings.credentials.lastfm_secret.get_secret_value.return_value = "test_secret"
            mock_settings.api.lastfm_rate_limit = 10.0  # Fast rate limit for test
            mock_settings.api.lastfm_concurrency = 50
            mock_settings.api.lastfm_request_timeout = 10.0
            
            client = LastFMAPIClient()
        
        print("\n🔬 Testing single pylast instance serialization")
        
        # Track call timings
        call_results = []
        
        async def timed_api_call(call_id: int) -> dict[str, Any]:
            """Make API call and track timing."""
            start_time = time.time()
            thread_id = threading.get_ident()
            
            print(f"   Call {call_id}: Starting on thread {thread_id}")
            
            result = await client.get_track(f"Artist_{call_id}", f"Track_{call_id}")
            
            end_time = time.time()
            duration = end_time - start_time
            
            call_data = {
                "call_id": call_id,
                "thread_id": thread_id,
                "start_time": start_time,
                "end_time": end_time,
                "duration": duration,
                "success": result is not None
            }
            
            print(f"   Call {call_id}: Completed in {duration:.2f}s on thread {thread_id}")
            call_results.append(call_data)
            return call_data
        
        # Test with 5 concurrent calls
        num_calls = 5
        overall_start = time.time()
        
        # Create all tasks at once
        tasks = [asyncio.create_task(timed_api_call(i)) for i in range(num_calls)]
        
        # Wait for all to complete
        await asyncio.gather(*tasks)
        overall_duration = time.time() - overall_start
        
        print("\n📊 Single Instance Results:")
        print(f"   Total time: {overall_duration:.2f}s")
        print("   Expected concurrent: ~1.0s (if truly parallel)")
        print("   Expected serial: ~5.0s (if serialized)")
        
        # Analyze thread usage
        unique_threads = len({r["thread_id"] for r in call_results})
        print(f"   Unique threads: {unique_threads}")
        
        # Check for overlapping execution
        overlaps = 0
        for i, r1 in enumerate(call_results):
            for j, r2 in enumerate(call_results):
                if i != j:
                    if r1["start_time"] < r2["end_time"] and r1["end_time"] > r2["start_time"]:
                        overlaps += 1
        
        concurrent_execution = overlaps > 0
        print(f"   Overlapping execution: {concurrent_execution}")
        
        # This should show serialization with single instance
        if overall_duration > 3.5:  # Close to 5s = serialized
            print("   🚨 SERIALIZATION CONFIRMED: Single pylast instance bottleneck")
        else:
            print("   ✅ CONCURRENT: Single instance allows concurrency")
        
        return {
            "overall_duration": overall_duration,
            "unique_threads": unique_threads,
            "concurrent_execution": concurrent_execution,
            "serialized": overall_duration > 3.5
        }

    @pytest.mark.asyncio
    async def test_multiple_pylast_instances_hypothesis(self, mock_pylast_slow_response):
        """Test if multiple pylast instances enable concurrency."""
        
        print("\n🔬 Testing multiple pylast instances hypothesis")
        
        def create_thread_local_client():
            """Create a new LastFM client for each call."""
            with patch('src.config.settings') as mock_settings:
                mock_settings.credentials.lastfm_key = "test_key"
                mock_settings.credentials.lastfm_secret.get_secret_value.return_value = "test_secret"
                mock_settings.api.lastfm_rate_limit = 10.0
                mock_settings.api.lastfm_concurrency = 50
                mock_settings.api.lastfm_request_timeout = 10.0
                
                return LastFMAPIClient()
        
        call_results = []
        
        async def timed_api_call_with_new_client(call_id: int) -> dict[str, Any]:
            """Make API call with a new client instance."""
            # Create separate client for this thread
            client = create_thread_local_client()
            
            start_time = time.time()
            thread_id = threading.get_ident()
            
            print(f"   Call {call_id}: Starting with new client on thread {thread_id}")
            
            result = await client.get_track(f"Artist_{call_id}", f"Track_{call_id}")
            
            end_time = time.time()
            duration = end_time - start_time
            
            call_data = {
                "call_id": call_id,
                "thread_id": thread_id,
                "start_time": start_time,
                "end_time": end_time,
                "duration": duration,
                "success": result is not None,
                "client_id": id(client)  # Track different client instances
            }
            
            print(f"   Call {call_id}: Completed in {duration:.2f}s (client {id(client)})")
            call_results.append(call_data)
            return call_data
        
        # Test with 5 concurrent calls using different clients
        num_calls = 5
        overall_start = time.time()
        
        tasks = [asyncio.create_task(timed_api_call_with_new_client(i)) for i in range(num_calls)]
        await asyncio.gather(*tasks)
        
        overall_duration = time.time() - overall_start
        
        print("\n📊 Multiple Instances Results:")
        print(f"   Total time: {overall_duration:.2f}s")
        print("   Expected concurrent: ~1.0s")
        print("   Expected serial: ~5.0s")
        
        unique_clients = len({r["client_id"] for r in call_results})
        unique_threads = len({r["thread_id"] for r in call_results})
        
        print(f"   Unique clients: {unique_clients}")
        print(f"   Unique threads: {unique_threads}")
        
        # Check for overlapping execution
        overlaps = 0
        for i, r1 in enumerate(call_results):
            for j, r2 in enumerate(call_results):
                if i != j:
                    if r1["start_time"] < r2["end_time"] and r1["end_time"] > r2["start_time"]:
                        overlaps += 1
        
        concurrent_execution = overlaps > 0
        print(f"   Overlapping execution: {concurrent_execution}")
        
        if overall_duration < 2.0:  # Much faster = concurrent
            print("   ✅ CONCURRENT: Multiple instances enable concurrency!")
        else:
            print("   ⚠️ STILL SERIALIZED: Multiple instances didn't help")
        
        return {
            "overall_duration": overall_duration,
            "unique_clients": unique_clients,
            "unique_threads": unique_threads,
            "concurrent_execution": concurrent_execution,
            "concurrent": overall_duration < 2.0
        }

    @pytest.mark.asyncio
    async def test_instance_comparison(self, mock_pylast_slow_response):
        """Compare single vs multiple instance performance."""
        
        print("\n⚖️ Comparing Single vs Multiple Instance Performance")
        
        # Test single instance
        single_result = await self.test_single_pylast_instance_serialization(mock_pylast_slow_response)
        
        # Test multiple instances  
        multiple_result = await self.test_multiple_pylast_instances_hypothesis(mock_pylast_slow_response)
        
        print("\n🏁 Performance Comparison:")
        print(f"   Single instance: {single_result['overall_duration']:.2f}s")
        print(f"   Multiple instances: {multiple_result['overall_duration']:.2f}s")
        
        speedup = single_result['overall_duration'] / multiple_result['overall_duration']
        print(f"   Speedup: {speedup:.1f}x")
        
        if speedup > 2.0:
            print("   🎯 HYPOTHESIS CONFIRMED: Multiple instances solve the bottleneck!")
            assert multiple_result['concurrent'], "Multiple instances should enable concurrency"
        else:
            print("   ❌ HYPOTHESIS REJECTED: Multiple instances don't help")
        
        return {
            "single_duration": single_result['overall_duration'],
            "multiple_duration": multiple_result['overall_duration'],
            "speedup": speedup,
            "hypothesis_confirmed": speedup > 2.0
        }