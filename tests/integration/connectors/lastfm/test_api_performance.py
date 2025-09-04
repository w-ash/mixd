"""Integration tests for LastFM API performance with real network calls.

These tests are marked as performance tests and excluded from regular runs
to keep test suite fast. Run with: pytest -m "performance"
"""

import asyncio
import os
import time

import pytest

from src.domain.entities import Artist, Track
from src.infrastructure.connectors._shared.request_start_gate import RequestStartGate
from src.infrastructure.connectors.lastfm.client import LastFMAPIClient
from src.infrastructure.connectors.lastfm.operations import LastFMOperations


@pytest.mark.performance
class TestLastFMAPIPerformance:
    """Performance tests with real LastFM API calls (requires credentials)."""

    @pytest.fixture
    def skip_if_no_credentials(self):
        """Skip tests if LastFM credentials not available."""
        if not os.getenv('LASTFM_KEY'):
            pytest.skip("LastFM credentials not configured - set LASTFM_KEY environment variable")

    @pytest.fixture
    def real_lastfm_client(self, skip_if_no_credentials):
        """LastFM client with real credentials for performance testing."""
        return LastFMAPIClient(
            api_key=os.getenv('LASTFM_KEY'),
            api_secret=os.getenv('LASTFM_SECRET', ''),
            lastfm_username=os.getenv('LASTFM_USERNAME', ''),
            request_gate=RequestStartGate(delay=0.25)  # Slightly slower for API respect
        )

    @pytest.fixture
    def real_lastfm_operations(self, real_lastfm_client):
        """LastFM operations with real client."""
        return LastFMOperations(client=real_lastfm_client)

    @pytest.mark.asyncio
    async def test_individual_api_call_timing(self, real_lastfm_client):
        """Test timing of individual real API calls."""
        timing_data = {}
        
        # Test well-known track that should exist
        test_cases = [
            ("The Beatles", "Hey Jude"),
            ("Queen", "Bohemian Rhapsody"),
            ("Led Zeppelin", "Stairway to Heaven"),
        ]
        
        for artist, track in test_cases:
            start_time = time.time()
            
            try:
                result = await real_lastfm_client.get_track(artist, track)
                duration = time.time() - start_time
                
                timing_data[f"{artist} - {track}"] = {
                    "duration": duration,
                    "success": result is not None,
                    "found": result is not None
                }
                
                print(f"✓ {artist} - {track}: {duration:.3f}s {'(found)' if result else '(not found)'}")
                
            except Exception as e:
                duration = time.time() - start_time
                timing_data[f"{artist} - {track}"] = {
                    "duration": duration,
                    "success": False,
                    "error": str(e)
                }
                print(f"✗ {artist} - {track}: {duration:.3f}s (error: {e})")
        
        # Analyze timing
        durations = [data["duration"] for data in timing_data.values() if data.get("success")]
        
        if durations:
            avg_duration = sum(durations) / len(durations)
            max_duration = max(durations)
            min_duration = min(durations)
            
            print("\n📊 API Call Timing Analysis:")
            print(f"   Average: {avg_duration:.3f}s")
            print(f"   Min:     {min_duration:.3f}s") 
            print(f"   Max:     {max_duration:.3f}s")
            
            # Individual calls should not take more than 2 seconds
            assert max_duration < 2.0, f"API calls too slow: max {max_duration:.3f}s"
            
            # Average should be reasonable
            assert avg_duration < 1.0, f"Average API time too slow: {avg_duration:.3f}s"
        
        else:
            pytest.fail("No successful API calls to analyze")

    @pytest.mark.asyncio
    async def test_concurrent_api_calls_with_rate_limiting(self, real_lastfm_client):
        """Test concurrent API calls with rate limiting."""
        
        # Test tracks (use different artists to avoid caching effects)
        test_tracks = [
            ("The Beatles", "Yesterday"),
            ("Queen", "We Will Rock You"),
            ("Led Zeppelin", "Black Dog"),
            ("Pink Floyd", "Wish You Were Here"),
            ("The Rolling Stones", "Paint It Black"),
        ]
        
        call_start_times = []
        call_end_times = []
        results = []
        
        async def timed_api_call(artist: str, track: str):
            start = time.time()
            call_start_times.append(start)
            
            try:
                result = await real_lastfm_client.get_track(artist, track)
                end = time.time()
                call_end_times.append(end)
                results.append({
                    "artist": artist,
                    "track": track,
                    "duration": end - start,
                    "success": result is not None,
                    "start_time": start,
                    "end_time": end
                })
                return result
                
            except Exception as e:
                end = time.time()
                call_end_times.append(end)
                results.append({
                    "artist": artist,
                    "track": track,
                    "duration": end - start,
                    "success": False,
                    "error": str(e),
                    "start_time": start,
                    "end_time": end
                })
                return None
        
        # Execute concurrent calls
        overall_start = time.time()
        tasks = [asyncio.create_task(timed_api_call(artist, track)) for artist, track in test_tracks]
        await asyncio.gather(*tasks, return_exceptions=True)
        overall_duration = time.time() - overall_start
        
        print("\n🚀 Concurrent API Test Results:")
        print(f"   Total time: {overall_duration:.3f}s")
        print(f"   Calls made: {len(results)}")
        
        successful_results = [r for r in results if r["success"]]
        print(f"   Successful: {len(successful_results)}")
        
        if len(successful_results) >= 2:
            # Check rate limiting - start times should be spaced
            start_gaps = [call_start_times[i] - call_start_times[i - 1] for i in range(1, len(call_start_times))]
            avg_start_gap = sum(start_gaps) / len(start_gaps)
            
            print(f"   Avg start gap: {avg_start_gap:.3f}s")
            
            # Should be close to our 250ms rate limit
            assert 0.2 < avg_start_gap < 0.35, f"Rate limiting not working: {avg_start_gap:.3f}s gap"
            
            # Check for concurrency - calls should overlap
            # Find maximum overlap
            max_concurrent = 0
            for i, result in enumerate(results):
                if not result["success"]:
                    continue
                    
                concurrent = 1
                for j, other in enumerate(results):
                    if i != j and other["success"]:
                        # Check if calls overlapped
                        if (result["start_time"] < other["end_time"] and 
                            result["end_time"] > other["start_time"]):
                            concurrent += 1
                
                max_concurrent = max(max_concurrent, concurrent)
            
            print(f"   Max concurrent: {max_concurrent}")
            assert max_concurrent > 1, "No concurrency detected"
            
            # Total time should be much less than sequential
            sequential_time = sum(r["duration"] for r in successful_results)
            efficiency = overall_duration / sequential_time
            print(f"   Efficiency: {efficiency:.2f} (1.0 = sequential, 0.2 = 5x speedup)")
            
            # Should achieve some parallelism  
            assert efficiency < 0.8, f"Not achieving parallelism: {efficiency:.2f} efficiency"

    @pytest.mark.asyncio
    async def test_batch_operations_performance(self, real_lastfm_operations):
        """Test batch operations performance with real API."""
        
        # Create test tracks for batch processing
        test_tracks = [
            Track(
                id=i,
                title=title,
                artists=[Artist(name=artist)],
                duration_ms=180000
            )
            for i, (artist, title) in enumerate([
                ("The Beatles", "Help!"),
                ("Queen", "Another One Bites The Dust"),
                ("Led Zeppelin", "Whole Lotta Love"),
                ("Pink Floyd", "Money"),
                ("The Rolling Stones", "Satisfaction"),
                ("AC/DC", "Back In Black"),
                ("Nirvana", "Smells Like Teen Spirit"),
                ("Radiohead", "Creep"),
            ])
        ]
        
        print(f"\n📦 Testing batch operations with {len(test_tracks)} tracks...")
        
        start_time = time.time()
        results = await real_lastfm_operations.batch_get_track_info(test_tracks)
        total_duration = time.time() - start_time
        
        print(f"   Total time: {total_duration:.3f}s")
        print(f"   Results: {len(results)}/{len(test_tracks)}")
        print(f"   Rate: {len(test_tracks) / total_duration:.2f} tracks/second")
        
        # Should complete within reasonable time
        assert total_duration < len(test_tracks) * 0.5, f"Batch too slow: {total_duration:.3f}s"
        
        # Should get results for most tracks
        success_rate = len(results) / len(test_tracks)
        print(f"   Success rate: {success_rate:.1%}")
        assert success_rate > 0.6, f"Low success rate: {success_rate:.1%}"
        
        # Calculate expected vs actual performance
        expected_sequential = len(test_tracks) * 0.5  # Assume 500ms per call
        speedup = expected_sequential / total_duration
        print(f"   Speedup vs sequential: {speedup:.1f}x")
        
        # Should achieve meaningful speedup
        assert speedup > 2.0, f"Insufficient speedup: {speedup:.1f}x"

    @pytest.mark.asyncio
    async def test_network_error_handling_performance(self, real_lastfm_client):
        """Test performance impact of network errors and timeouts."""
        
        error_results = []
        
        # Test with invalid tracks that might cause errors
        error_test_cases = [
            ("NonExistentArtist12345", "NonExistentTrack67890"),
            ("", ""),  # Empty strings
            ("Artist" * 100, "Track" * 100),  # Very long strings
        ]
        
        for artist, track in error_test_cases:
            start_time = time.time()
            
            try:
                result = await asyncio.wait_for(
                    real_lastfm_client.get_track(artist, track),
                    timeout=5.0  # 5 second timeout
                )
                duration = time.time() - start_time
                
                error_results.append({
                    "case": f"{artist[:20]}... - {track[:20]}...",
                    "duration": duration,
                    "result": "success" if result else "not_found",
                    "timed_out": False
                })
                
            except TimeoutError:
                duration = time.time() - start_time
                error_results.append({
                    "case": f"{artist[:20]}... - {track[:20]}...",
                    "duration": duration,
                    "result": "timeout",
                    "timed_out": True
                })
                
            except Exception as e:
                duration = time.time() - start_time
                error_results.append({
                    "case": f"{artist[:20]}... - {track[:20]}...",
                    "duration": duration,
                    "result": f"error: {type(e).__name__}",
                    "timed_out": False
                })
        
        print("\n⚠️ Error Handling Performance:")
        for result in error_results:
            print(f"   {result['case']}: {result['duration']:.3f}s ({result['result']})")
        
        # No call should take longer than timeout
        max_duration = max(r["duration"] for r in error_results)
        assert max_duration < 6.0, f"Error handling too slow: {max_duration:.3f}s"
        
        # Most calls should complete within reasonable time even on error
        fast_calls = [r for r in error_results if r["duration"] < 2.0]
        assert len(fast_calls) >= len(error_results) * 0.6, "Too many slow error responses"

    def test_performance_test_configuration(self):
        """Test that performance tests are properly configured."""
        import pytest
        
        # This test should be marked as performance
        markers = pytest.current_pytest_node().iter_markers("performance")
        assert list(markers), "Performance test not properly marked"
        
        # Verify environment variables are available for CI/testing
        required_env_hints = [
            "LASTFM_KEY",
            "LASTFM_SECRET", 
            "LASTFM_USERNAME"
        ]
        
        available_vars = [var for var in required_env_hints if os.getenv(var)]
        
        print("\n🔧 Performance Test Configuration:")
        print(f"   Environment variables: {len(available_vars)}/{len(required_env_hints)} available")
        
        if not available_vars:
            print("   ⚠️ No LastFM credentials configured")
            print("   Set LASTFM_KEY, LASTFM_SECRET, LASTFM_USERNAME to enable API tests")
        else:
            print("   ✓ Credentials configured for API testing")