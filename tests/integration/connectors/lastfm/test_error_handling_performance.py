"""Performance tests for LastFM error handling to ensure error classification doesn't impact speed.

These tests verify that the enhanced error handling with classification doesn't 
introduce performance regressions compared to basic error handling.
"""

import asyncio
import time
from unittest.mock import MagicMock, patch

import pylast
import pytest

from src.infrastructure.connectors.lastfm.client import LastFMAPIClient


@pytest.mark.integration
@pytest.mark.performance  
class TestLastFMErrorHandlingPerformance:
    """Performance tests for error handling in LastFM comprehensive API methods."""

    @pytest.fixture
    def mock_settings(self):
        """Mock settings for LastFM client configuration."""
        with patch('src.config.settings') as mock_settings:
            mock_settings.credentials.lastfm_key = "test_key"
            mock_settings.credentials.lastfm_secret.get_secret_value.return_value = "test_secret"
            mock_settings.credentials.lastfm_username = "test_user"
            mock_settings.credentials.lastfm_password.get_secret_value.return_value = "test_pass"
            mock_settings.api.lastfm_rate_limit = 100.0  # High rate for performance testing
            mock_settings.api.lastfm_concurrency = 50
            mock_settings.api.lastfm_request_timeout = 10.0
            yield mock_settings

    @pytest.fixture
    def lastfm_client(self, mock_settings):
        """LastFM client with proper configuration."""
        return LastFMAPIClient()

    @pytest.mark.asyncio
    async def test_successful_calls_performance_baseline(self, lastfm_client):
        """Baseline performance test for successful comprehensive API calls."""
        
        def mock_successful_fast_call(*args, **kwargs):
            """Mock that returns immediately (simulates fast API)."""
            mock_track = MagicMock(spec=pylast.Track)
            mock_track._request.return_value = MagicMock()
            return mock_track
        
        mock_data = {
            'lastfm_title': 'Fast Track',
            'lastfm_artist_name': 'Fast Artist', 
            'lastfm_global_playcount': 1000
        }
        
        with patch('pylast.LastFMNetwork') as mock_network_class, \
             patch.object(LastFMAPIClient, '_get_comprehensive_track_data', return_value=mock_data):
            
            mock_network = MagicMock()
            mock_network.get_track.side_effect = mock_successful_fast_call
            mock_network_class.return_value = mock_network
            
            # Measure time for 20 successful calls
            num_calls = 20
            start_time = time.time()
            
            tasks = []
            for i in range(num_calls):
                task = lastfm_client.get_track_info_comprehensive(f"Artist{i}", f"Track{i}")
                tasks.append(task)
            
            results = await asyncio.gather(*tasks)
            
            end_time = time.time()
            duration = end_time - start_time
            
            # Verify all succeeded
            assert len(results) == num_calls
            assert all(r is not None for r in results)
            
            # Performance assertion - should be very fast for mocked calls
            assert duration < 2.0, f"Successful calls took {duration:.2f}s, expected < 2.0s"
            
            calls_per_second = num_calls / duration
            assert calls_per_second > 10, f"Only {calls_per_second:.1f} calls/sec, expected > 10"
            
            print(f"✅ Successful calls: {num_calls} calls in {duration:.2f}s ({calls_per_second:.1f} calls/sec)")

    @pytest.mark.asyncio
    async def test_not_found_errors_performance(self, lastfm_client):
        """Test that not_found errors are handled quickly (no retries)."""
        
        def mock_not_found_call(*args, **kwargs):
            """Mock that immediately raises not found error."""
            raise pylast.WSError("LastFm", "999", "Track not found")
        
        with patch('pylast.LastFMNetwork') as mock_network_class:
            mock_network = MagicMock()
            mock_network.get_track.side_effect = mock_not_found_call
            mock_network_class.return_value = mock_network
            
            # Measure time for 20 not found errors
            num_calls = 20
            start_time = time.time()
            
            tasks = []
            for i in range(num_calls):
                task = lastfm_client.get_track_info_comprehensive(f"Missing{i}", f"Track{i}")
                tasks.append(task)
            
            results = await asyncio.gather(*tasks)
            
            end_time = time.time()
            duration = end_time - start_time
            
            # Verify all returned None (not found)
            assert len(results) == num_calls
            assert all(r is None for r in results)
            
            # Should be fast since no retries
            assert duration < 1.0, f"Not found errors took {duration:.2f}s, expected < 1.0s"
            
            # Verify only one call per track (no retries)
            assert mock_network.get_track.call_count == num_calls
            
            print(f"✅ Not found errors: {num_calls} calls in {duration:.2f}s (no retries)")

    @pytest.mark.asyncio
    async def test_permanent_errors_performance(self, lastfm_client):
        """Test that permanent errors are handled quickly (no retries)."""
        
        call_count = 0
        
        def mock_permanent_error_call(*args, **kwargs):
            """Mock that raises permanent error (invalid API key)."""
            nonlocal call_count
            call_count += 1
            raise pylast.WSError("LastFm", "10", "Invalid API key")
        
        with patch('pylast.LastFMNetwork') as mock_network_class:
            mock_network = MagicMock()
            mock_network.get_track.side_effect = mock_permanent_error_call
            mock_network_class.return_value = mock_network
            
            # Measure time for 10 permanent errors
            num_calls = 10
            start_time = time.time()
            
            tasks = []
            for i in range(num_calls):
                task = lastfm_client.get_track_info_comprehensive(f"Artist{i}", f"Track{i}")
                tasks.append(task)
            
            results = await asyncio.gather(*tasks)
            
            end_time = time.time()
            duration = end_time - start_time
            
            # Verify all returned None (permanent error)
            assert len(results) == num_calls
            assert all(r is None for r in results)
            
            # Should be fast since no retries for permanent errors
            assert duration < 0.5, f"Permanent errors took {duration:.2f}s, expected < 0.5s"
            
            # Verify only one call per track (no retries for permanent errors)
            assert call_count == num_calls
            
            print(f"✅ Permanent errors: {num_calls} calls in {duration:.2f}s (no retries)")

    @pytest.mark.asyncio
    async def test_mixed_scenario_performance(self, lastfm_client):
        """Test performance with mixed success/error scenarios."""
        
        def mock_mixed_scenario_call(*args, **kwargs):
            """Mock that returns different responses based on track name."""
            _artist, track = args
            
            if "success" in track.lower():
                mock_track = MagicMock(spec=pylast.Track)
                mock_track._request.return_value = MagicMock()
                return mock_track
            elif "notfound" in track.lower():
                raise pylast.WSError("LastFm", "999", "Track not found")
            elif "permanent" in track.lower():
                raise pylast.WSError("LastFm", "10", "Invalid API key")
            else:
                # Default to success
                mock_track = MagicMock(spec=pylast.Track)
                mock_track._request.return_value = MagicMock()
                return mock_track
        
        mock_success_data = {'title': 'success', 'found': True}
        
        with patch('pylast.LastFMNetwork') as mock_network_class, \
             patch.object(LastFMAPIClient, '_get_comprehensive_track_data', return_value=mock_success_data):
            
            mock_network = MagicMock()
            mock_network.get_track.side_effect = mock_mixed_scenario_call
            mock_network_class.return_value = mock_network
            
            # Create mixed test scenarios
            test_scenarios = [
                ("Artist1", "Success Track 1"),  # Success
                ("Artist2", "NotFound Track"),   # Not found error
                ("Artist3", "Success Track 2"),  # Success
                ("Artist4", "Permanent Error"),  # Permanent error
                ("Artist5", "Success Track 3"),  # Success
                ("Artist6", "NotFound Again"),   # Not found error
                ("Artist7", "Success Track 4"),  # Success
            ]
            
            start_time = time.time()
            
            tasks = []
            for artist, track in test_scenarios:
                task = lastfm_client.get_track_info_comprehensive(artist, track)
                tasks.append(task)
            
            results = await asyncio.gather(*tasks)
            
            end_time = time.time()
            duration = end_time - start_time
            
            # Count results
            successful_results = [r for r in results if r is not None]
            failed_results = [r for r in results if r is None]
            
            # Should have 4 successes, 3 failures
            assert len(successful_results) == 4
            assert len(failed_results) == 3
            
            # Should be reasonably fast (no retries for errors)
            assert duration < 1.5, f"Mixed scenario took {duration:.2f}s, expected < 1.5s"
            
            success_rate = len(successful_results) / len(results) * 100
            print(f"✅ Mixed scenario: {len(results)} calls in {duration:.2f}s ({success_rate:.1f}% success rate)")

    @pytest.mark.asyncio 
    async def test_error_classification_overhead(self, lastfm_client):
        """Test that error classification doesn't add significant overhead."""
        
        def mock_simple_not_found(*args, **kwargs):
            """Simple not found error for overhead testing."""
            raise pylast.WSError("LastFm", "999", "not found")
        
        with patch('pylast.LastFMNetwork') as mock_network_class:
            mock_network = MagicMock()
            mock_network.get_track.side_effect = mock_simple_not_found
            mock_network_class.return_value = mock_network
            
            # Test a larger batch to measure overhead
            num_calls = 50
            
            start_time = time.time()
            
            # Process sequentially to avoid concurrency masking overhead
            results = []
            for i in range(num_calls):
                result = await lastfm_client.get_track_info_comprehensive(f"Artist{i}", f"Track{i}")
                results.append(result)
            
            end_time = time.time()
            duration = end_time - start_time
            
            # All should be None (not found)
            assert all(r is None for r in results)
            
            # Calculate per-call overhead
            overhead_per_call = duration / num_calls
            
            # Should be minimal overhead per call (mostly error classification time)
            assert overhead_per_call < 0.01, f"Error classification overhead {overhead_per_call:.3f}s per call, expected < 0.01s"
            
            print(f"✅ Error classification overhead: {overhead_per_call:.3f}s per call for {num_calls} calls")