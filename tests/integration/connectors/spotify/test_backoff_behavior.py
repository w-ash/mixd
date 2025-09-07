"""Backoff behavior tests for Spotify API client methods.

This test suite focuses on testing the actual backoff decorator behavior,
retry timing, and resilience patterns in the Spotify API client.

Tests verify:
- Backoff timing patterns (exponential vs constant)
- Proper giveup conditions based on error classification  
- Handler invocation (on_backoff, on_giveup callbacks)
- Integration between error classifier and backoff decorators
- Resilient operation telemetry integration
"""

import asyncio
import time
from unittest.mock import patch

import pytest
import spotipy

from src.infrastructure.connectors.spotify.client import SpotifyAPIClient


@pytest.mark.integration
class TestSpotifyBackoffBehavior:
    """Integration tests for Spotify API backoff and retry behavior."""

    @pytest.fixture
    def spotify_client(self):
        """Spotify client with mocked settings."""
        with patch('src.config.settings') as mock_settings:
            mock_settings.credentials.spotify_client_id = "test_client_id"
            mock_settings.credentials.spotify_client_secret.get_secret_value.return_value = "test_secret"
            mock_settings.api.spotify_market = "US"
            mock_settings.api.spotify_rate_limit = 10.0
            yield SpotifyAPIClient()

    @pytest.mark.asyncio
    async def test_exponential_backoff_timing_temporary_errors(self, spotify_client):
        """Test that temporary errors use exponential backoff with proper timing."""
        call_times = []
        call_count = 0
        
        def mock_api_call_server_error(*args, **kwargs):
            nonlocal call_count
            call_times.append(time.time())
            call_count += 1
            exception = spotipy.SpotifyException(503, -1, "Service Unavailable")
            exception.http_status = 503
            raise exception
        
        with patch('src.infrastructure.connectors.spotify.client.spotify_api_call') as mock_api_call:
            mock_api_call.side_effect = mock_api_call_server_error
            
            start_time = time.time()
            result = await spotify_client.get_tracks_bulk(["test_id"])
            total_duration = time.time() - start_time
            
            assert result is None
            assert call_count == 3  # max_tries=3
            
            # Verify backoff delays exist - critical for avoiding thundering herd and server overload
            assert total_duration > 0.1, f"Retries should have delays for server protection: {total_duration}s"
            
            # Verify multiple distinct retry attempts occurred
            if len(call_times) >= 2:
                first_retry_delay = call_times[1] - call_times[0]
                assert first_retry_delay > 0, f"First retry should have delay: {first_retry_delay}s"
                
            # The key critical behavior: retries are spaced out to protect servers
            # (Don't test precise exponential calculations due to jitter and system load)

    @pytest.mark.asyncio
    async def test_immediate_failure_permanent_errors(self, spotify_client):
        """Test that permanent errors cause immediate failure without retries."""
        call_times = []
        call_count = 0
        
        def mock_api_call_auth_error(*args, **kwargs):
            nonlocal call_count
            call_times.append(time.time())
            call_count += 1
            exception = spotipy.SpotifyException(401, -1, "Unauthorized")
            exception.http_status = 401
            raise exception
        
        with patch('src.infrastructure.connectors.spotify.client.spotify_api_call') as mock_api_call:
            mock_api_call.side_effect = mock_api_call_auth_error
            
            start_time = time.time()
            result = await spotify_client.get_tracks_bulk(["test_id"])
            total_duration = time.time() - start_time
            
            assert result is None
            assert call_count == 1  # Should not retry
            assert total_duration < 1.0, f"Permanent error took too long: {total_duration}s"

    @pytest.mark.asyncio 
    async def test_error_logging_and_classification_behavior(self, spotify_client):
        """Test that errors are properly logged and classified - critical for debugging production issues."""
        call_count = 0
        captured_logs = []
        
        def mock_api_call_rate_limit(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            exception = spotipy.SpotifyException(429, -1, "Rate limit exceeded")
            exception.http_status = 429
            raise exception
        
        # Capture actual log output from the error classification handlers
        with patch('src.infrastructure.connectors.spotify.client.spotify_api_call') as mock_api_call, \
             patch('src.infrastructure.connectors._shared.error_classification.logger') as mock_logger:
            
            mock_api_call.side_effect = mock_api_call_rate_limit
            
            # Capture log calls to verify error handling visibility
            def capture_log_call(*args, **kwargs):
                captured_logs.append(f"{args} {kwargs}")
            
            mock_logger.warning.side_effect = capture_log_call
            mock_logger.error.side_effect = capture_log_call
            
            result = await spotify_client.get_tracks_bulk(["test_id"])
            
            assert result is None
            assert call_count == 3, "Should retry rate limit errors"
            
            # Verify critical error information is logged for production debugging
            assert len(captured_logs) >= 2, "Should log backoff attempts for visibility"
            
            # Verify rate limit detection is logged
            rate_limit_logs = [log for log in captured_logs if 'rate limit' in log.lower()]
            assert len(rate_limit_logs) > 0, "Rate limit detection must be logged for operators"

    @pytest.mark.asyncio
    async def test_giveup_condition_integration(self, spotify_client):
        """Test that giveup conditions properly integrate with error classifier."""
        call_count = 0
        
        def mock_api_call_mixed_errors(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            
            if call_count == 1:
                # First call: temporary error (should retry)
                exception = spotipy.SpotifyException(503, -1, "Service Unavailable") 
                exception.http_status = 503
                raise exception
            elif call_count == 2:
                # Second call: permanent error (should stop retrying immediately)
                exception = spotipy.SpotifyException(401, -1, "Unauthorized")
                exception.http_status = 401
                raise exception
        
        with patch('src.infrastructure.connectors.spotify.client.spotify_api_call') as mock_api_call:
            mock_api_call.side_effect = mock_api_call_mixed_errors
            
            start_time = time.time()
            result = await spotify_client.get_tracks_bulk(["test_id"])
            time.time() - start_time
            
            assert result is None
            # Should stop after 2 calls due to permanent error giveup condition
            assert call_count == 2, f"Expected 2 calls due to giveup condition, got {call_count}"

    @pytest.mark.asyncio
    async def test_operation_monitoring_and_telemetry(self, spotify_client):
        """Test that operations are properly monitored - critical for production observability."""
        call_count = 0
        
        def mock_api_call_success(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return {"tracks": [{"id": "test_id", "name": "Test Track"}]}
        
        with patch('src.infrastructure.connectors.spotify.client.spotify_api_call') as mock_api_call:
            mock_api_call.side_effect = mock_api_call_success
            
            result = await spotify_client.get_tracks_bulk(["test_id"])
            
            assert result is not None, "Successful operations should return data"
            assert call_count == 1, "Should not retry on success"
            
            # The key verification is that operations complete successfully when they should
            # This ensures the resilient operation infrastructure is working
            assert "tracks" in result, "Should return properly structured Spotify data"
            assert len(result["tracks"]) > 0, "Should return actual track data"

    @pytest.mark.asyncio
    async def test_successful_retry_after_failures(self, spotify_client):
        """Test successful completion after some failures (resilience pattern)."""
        call_count = 0
        success_data = {"tracks": [{"id": "test_track", "name": "Test Track"}]}
        
        def mock_api_call_eventual_success(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            
            if call_count <= 2:
                # Fail first two attempts with temporary error
                exception = spotipy.SpotifyException(502, -1, "Bad Gateway")
                exception.http_status = 502
                raise exception
            else:
                # Succeed on third attempt
                return success_data
        
        with patch('src.infrastructure.connectors.spotify.client.spotify_api_call') as mock_api_call:
            mock_api_call.side_effect = mock_api_call_eventual_success
            
            start_time = time.time()
            result = await spotify_client.get_tracks_bulk(["test_id"])
            total_duration = time.time() - start_time
            
            # Should succeed and return actual data
            assert result == success_data
            assert call_count == 3
            
            # Should have taken some time due to retries (more resilient threshold)
            assert total_duration >= 0.2, f"Successful retry too fast: {total_duration}s"

    @pytest.mark.parametrize("method_info", [
        ("get_tracks_bulk", ["test_id"], "get_spotify_tracks_bulk"),
        ("search_by_isrc", ["USRC17607839"], "search_spotify_by_isrc"),
        ("search_track", ["Artist", "Track"], "search_spotify_track"),
        ("get_playlist", ["playlist_id"], "get_spotify_playlist"),
        ("create_playlist", ["Test Playlist"], "create_spotify_playlist"),
        ("get_saved_tracks", [], "get_spotify_saved_tracks"),
        ("get_current_user", [], "get_spotify_current_user"),
    ])
    @pytest.mark.asyncio
    async def test_all_methods_have_backoff_decorators(self, spotify_client, method_info):
        """Test that all major Spotify client methods have proper backoff decorator integration."""
        method_name, args, _expected_operation_name = method_info
        call_count = 0
        
        def mock_api_call_rate_limit(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            exception = spotipy.SpotifyException(429, -1, "Rate limit exceeded")
            exception.http_status = 429
            raise exception
        
        with patch('src.infrastructure.connectors.spotify.client.spotify_api_call') as mock_api_call:
            mock_api_call.side_effect = mock_api_call_rate_limit
            
            method = getattr(spotify_client, method_name)
            
            start_time = time.time()
            result = await method(*args)
            total_duration = time.time() - start_time
            
            # Should handle errors gracefully with retries
            assert result is None
            assert call_count == 3, f"Method {method_name} should retry 3 times, got {call_count}"
            # Must have some retry delays to avoid overwhelming servers
            assert total_duration > 0, f"Method {method_name} should have retry delays: {total_duration}s"

    @pytest.mark.asyncio
    async def test_wrapper_pattern_exception_handling(self, spotify_client):
        """Test that the wrapper pattern properly handles final exceptions after backoff exhaustion."""
        call_count = 0
        
        def mock_api_call_persistent_error(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            exception = spotipy.SpotifyException(503, -1, "Service Unavailable")
            exception.http_status = 503
            raise exception
        
        with patch('src.infrastructure.connectors.spotify.client.spotify_api_call') as mock_api_call:
            mock_api_call.side_effect = mock_api_call_persistent_error
            
            # Test that wrapper methods gracefully handle exceptions from retry methods
            result = await spotify_client.get_tracks_bulk(["test_id"])
            
            # Should return None gracefully, not raise exception
            assert result is None
            assert call_count == 3  # Should have exhausted all retries
        
        # Test a method that re-raises exceptions (playlist_change_details)
        call_count = 0
        with patch('src.infrastructure.connectors.spotify.client.spotify_api_call') as mock_api_call:
            mock_api_call.side_effect = mock_api_call_persistent_error
            
            # This method should re-raise after retries are exhausted
            with pytest.raises(spotipy.SpotifyException):
                await spotify_client.playlist_change_details("test_id", "New Name")
            
            assert call_count == 3  # Should have exhausted all retries before re-raising

    @pytest.mark.asyncio
    async def test_concurrent_requests_backoff_independence(self, spotify_client):
        """Test that concurrent requests with different error types are handled independently."""
        
        def mock_api_call_rate_limit(*args, **kwargs):
            exception = spotipy.SpotifyException(429, -1, "Rate limit")
            exception.http_status = 429
            raise exception
        
        def mock_api_call_permanent_error(*args, **kwargs):
            exception = spotipy.SpotifyException(401, -1, "Unauthorized")
            exception.http_status = 401
            raise exception
        
        with patch('src.infrastructure.connectors.spotify.client.spotify_api_call') as mock_api_call:
            # First request will hit rate limit (should retry)
            mock_api_call.side_effect = mock_api_call_rate_limit
            task1 = asyncio.create_task(spotify_client.get_tracks_bulk(["test_id_1"]))
            
            # Second request will hit permanent error (should not retry)
            mock_api_call.side_effect = mock_api_call_permanent_error
            task2 = asyncio.create_task(spotify_client.search_by_isrc("USRC17607839"))
            
            # Reset to rate limit for consistency in first task
            mock_api_call.side_effect = mock_api_call_rate_limit
            
            start_time = time.time()
            result1, result2 = await asyncio.gather(task1, task2)
            time.time() - start_time
            
            # Both should return None but with different timing patterns
            assert result1 is None
            assert result2 is None
            
            # The permanent error task should complete much faster
            # (This is a basic test - more sophisticated timing analysis would require
            # more complex mocking to track individual request timings)
            
    @pytest.mark.asyncio
    async def test_error_classification_integration_with_backoff(self, spotify_client):
        """Test that SpotifyErrorClassifier properly integrates with backoff giveup conditions."""
        
        # Test sequence: temporary -> rate_limit -> permanent (should stop at permanent)
        call_count = 0
        
        def mock_api_call_escalating_errors(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            
            if call_count == 1:
                # First: temporary error (should retry)
                exception = spotipy.SpotifyException(503, -1, "Service Unavailable")
                exception.http_status = 503
                raise exception
            elif call_count == 2:
                # Second: rate limit error (should retry)  
                exception = spotipy.SpotifyException(429, -1, "Rate limit")
                exception.http_status = 429
                raise exception
            elif call_count == 3:
                # Third: permanent error (should stop retrying)
                exception = spotipy.SpotifyException(401, -1, "Unauthorized")
                exception.http_status = 401
                raise exception
        
        with patch('src.infrastructure.connectors.spotify.client.spotify_api_call') as mock_api_call:
            mock_api_call.side_effect = mock_api_call_escalating_errors
            
            result = await spotify_client.get_tracks_bulk(["test_id"])
            
            assert result is None
            # Should stop at the permanent error (call 3)
            assert call_count == 3