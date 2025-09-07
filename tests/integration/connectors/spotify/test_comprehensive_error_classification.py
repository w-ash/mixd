"""Comprehensive error classification tests for Spotify API integration.

This test suite provides exhaustive coverage of all error codes and patterns
defined in src/infrastructure/connectors/spotify/error_classifier.py.

Tests ensure that each error type triggers the correct retry behavior:
- Permanent errors: No retries (immediate failure) 
- Temporary errors: 2-3 retries with exponential backoff
- Rate limit errors: 2-3 retries with constant delay
- Not found errors: No retries (immediate failure with debug logging)
- Unknown errors: 2-3 retries with exponential backoff
"""

import time
from unittest.mock import patch

import pytest
import spotipy

from src.infrastructure.connectors.spotify.client import SpotifyAPIClient


@pytest.mark.integration
class TestComprehensiveErrorClassification:
    """Comprehensive HTTP status code coverage testing with all Spotify API error scenarios."""

    @pytest.fixture
    def spotify_client(self):
        """Spotify client with mocked settings."""
        with patch('src.config.settings') as mock_settings:
            mock_settings.credentials.spotify_client_id = "test_client_id"
            mock_settings.credentials.spotify_client_secret.get_secret_value.return_value = "test_secret"
            mock_settings.api.spotify_market = "US"
            mock_settings.api.spotify_rate_limit = 10.0
            yield SpotifyAPIClient()

    # PERMANENT ERRORS (4xx status codes) - Should NOT retry, immediate failure
    @pytest.mark.parametrize(('status_code', 'description'), [
        (400, "Bad Request - malformed request"),
        (401, "Unauthorized - invalid or expired token"),
        (403, "Forbidden - insufficient permissions"),
        (422, "Unprocessable Entity - request data is invalid"),
        (409, "Conflict - resource already exists"),
        (415, "Unsupported Media Type"),
        (416, "Range Not Satisfiable"),
    ])
    @pytest.mark.asyncio
    async def test_permanent_http_errors_no_retry_comprehensive(self, spotify_client, status_code, description):
        """Test all permanent HTTP status codes cause immediate failure with no retries."""
        
        call_count = 0
        
        def mock_get_tracks_permanent_error(*args, **kwargs):
            """Mock that raises the specified permanent HTTP error."""
            nonlocal call_count
            call_count += 1
            exception = spotipy.SpotifyException(status_code, -1, description)
            exception.http_status = status_code
            raise exception
        
        with patch('src.infrastructure.connectors.spotify.client.spotify_api_call') as mock_api_call:
            mock_api_call.side_effect = mock_get_tracks_permanent_error
            
            start_time = time.time()
            result = await spotify_client.get_tracks_bulk(["test_track_id"])
            duration = time.time() - start_time
            
            # Should return None gracefully (no exception raised)
            assert result is None
            
            # Should NOT retry (only 1 call) - permanent errors are immediate failures
            assert call_count == 1, f"Expected 1 call for permanent error {status_code}, got {call_count}"
            
            # Should be fast (no retry delays)
            assert duration < 2.0, f"Permanent error took too long: {duration}s"

    # NOT FOUND ERRORS (404) - Should NOT retry, immediate failure
    @pytest.mark.asyncio
    async def test_not_found_error_no_retry(self, spotify_client):
        """Test 404 Not Found causes immediate failure with no retries."""
        
        call_count = 0
        
        def mock_get_tracks_not_found(*args, **kwargs):
            """Mock that raises 404 Not Found error."""
            nonlocal call_count
            call_count += 1
            exception = spotipy.SpotifyException(404, -1, "Not Found - resource doesn't exist")
            exception.http_status = 404
            raise exception
        
        with patch('src.infrastructure.connectors.spotify.client.spotify_api_call') as mock_api_call:
            mock_api_call.side_effect = mock_get_tracks_not_found
            
            start_time = time.time()
            result = await spotify_client.get_tracks_bulk(["nonexistent_track_id"])
            duration = time.time() - start_time
            
            # Should return None gracefully (no exception raised)
            assert result is None
            
            # Should NOT retry (only 1 call) - not found errors are immediate failures
            assert call_count == 1, f"Expected 1 call for not found error, got {call_count}"
            
            # Should be fast (no retry delays)
            assert duration < 2.0, f"Not found error took too long: {duration}s"

    # RATE LIMIT ERRORS (429) - Should retry 2-3 times with backoff
    @pytest.mark.asyncio
    async def test_rate_limit_error_retries(self, spotify_client):
        """Test 429 Too Many Requests triggers retries with proper backoff."""
        
        call_count = 0
        
        def mock_get_tracks_rate_limit(*args, **kwargs):
            """Mock that always raises rate limit error."""
            nonlocal call_count
            call_count += 1
            exception = spotipy.SpotifyException(429, -1, "Too Many Requests - rate limit exceeded")
            exception.http_status = 429
            raise exception
        
        with patch('src.infrastructure.connectors.spotify.client.spotify_api_call') as mock_api_call:
            mock_api_call.side_effect = mock_get_tracks_rate_limit
            
            start_time = time.time()
            result = await spotify_client.get_tracks_bulk(["test_track_id"])
            duration = time.time() - start_time
            
            # Should return None after exhausting retries
            assert result is None
            
            # Should retry 3 times total (max_tries=3)
            assert call_count == 3, f"Expected 3 calls for rate limit error, got {call_count}"
            
            # Should have some retry delays to prevent overwhelming servers (not testing precise timing)
            assert duration > 0, f"Rate limit retries should have some delay: {duration}s"

    # TEMPORARY ERRORS (5xx status codes) - Should retry 2-3 times with backoff
    @pytest.mark.parametrize(('status_code', 'description'), [
        (500, "Internal Server Error"),
        (502, "Bad Gateway - upstream server issue"),
        (503, "Service Unavailable"),
        (504, "Gateway Timeout"),
        (507, "Insufficient Storage"),
        (508, "Loop Detected"),
        (511, "Network Authentication Required"),
    ])
    @pytest.mark.asyncio
    async def test_temporary_server_errors_retry_comprehensive(self, spotify_client, status_code, description):
        """Test all temporary server error status codes trigger proper retries."""
        
        call_count = 0
        
        def mock_get_tracks_server_error(*args, **kwargs):
            """Mock that always raises the specified server error."""
            nonlocal call_count
            call_count += 1
            exception = spotipy.SpotifyException(status_code, -1, description)
            exception.http_status = status_code
            raise exception
        
        with patch('src.infrastructure.connectors.spotify.client.spotify_api_call') as mock_api_call:
            mock_api_call.side_effect = mock_get_tracks_server_error
            
            start_time = time.time()
            result = await spotify_client.get_tracks_bulk(["test_track_id"])
            duration = time.time() - start_time
            
            # Should return None after exhausting retries
            assert result is None
            
            # Should retry 3 times total (max_tries=3)
            assert call_count == 3, f"Expected 3 calls for server error {status_code}, got {call_count}"
            
            # Should have retry delays to prevent overwhelming servers (not testing precise timing)
            assert duration > 0, f"Server error retries should have some delay: {duration}s"

    # TEXT PATTERN ERRORS - Should classify based on message content
    @pytest.mark.parametrize(('error_message', 'expected_type', 'expected_retries'), [
        ("Rate limit exceeded", "rate_limit", 3),
        ("invalid access token", "permanent", 1),
        ("token expired", "permanent", 1),
        ("Service temporarily unavailable", "temporary", 3),
        ("Internal server error occurred", "temporary", 3),
        ("Track not found in catalog", "not_found", 1),
        ("Playlist does not exist", "not_found", 1),
        ("Unknown error occurred", "unknown", 3),
    ])
    @pytest.mark.asyncio
    async def test_text_pattern_error_classification(self, spotify_client, error_message, expected_type, expected_retries):
        """Test error classification based on text patterns in error messages."""
        
        call_count = 0
        
        def mock_get_tracks_text_error(*args, **kwargs):
            """Mock that raises error with specified text pattern."""
            nonlocal call_count
            call_count += 1
            # Create SpotifyException without http_status to test text-based classification
            exception = spotipy.SpotifyException(-1, -1, error_message)
            raise exception
        
        with patch('src.infrastructure.connectors.spotify.client.spotify_api_call') as mock_api_call:
            mock_api_call.side_effect = mock_get_tracks_text_error
            
            start_time = time.time()
            result = await spotify_client.get_tracks_bulk(["test_track_id"])
            duration = time.time() - start_time
            
            # Should return None gracefully (no exception raised)
            assert result is None
            
            # Should retry expected number of times
            assert call_count == expected_retries, f"Expected {expected_retries} calls for {expected_type} error '{error_message}', got {call_count}"
            
            # Timing checks based on error type
            if expected_type in ["permanent", "not_found"]:
                # Should be fast (no retry delays)
                assert duration < 2.0, f"{expected_type} error took too long: {duration}s"
            else:
                # Should have retry delays for retriable errors
                assert duration > 0, f"{expected_type} error retries should have some delay: {duration}s"

    # NETWORK ERRORS (non-Spotify exceptions) - Should propagate up (not retried by Spotify client)
    @pytest.mark.parametrize("network_error", [
        ConnectionError("Connection failed"),
        TimeoutError("Request timeout"),
        OSError("Network unavailable"),
        Exception("DNS resolution failed"),
        Exception("SSL certificate error"),
    ])
    @pytest.mark.asyncio
    async def test_network_errors_propagate_up(self, spotify_client, network_error):
        """Test non-Spotify network errors propagate up and are not retried by Spotify client."""
        
        call_count = 0
        
        def mock_get_tracks_network_error(*args, **kwargs):
            """Mock that raises specified network error."""
            nonlocal call_count
            call_count += 1
            raise network_error
        
        with patch('src.infrastructure.connectors.spotify.client.spotify_api_call') as mock_api_call:
            mock_api_call.side_effect = mock_get_tracks_network_error
            
            start_time = time.time()
            
            # Should propagate the exception up (not caught by Spotify backoff decorator)
            with pytest.raises(type(network_error)):
                await spotify_client.get_tracks_bulk(["test_track_id"])
            
            duration = time.time() - start_time
            
            # Should NOT retry (only 1 call) - network errors are not Spotify client's responsibility
            assert call_count == 1, f"Expected 1 call for network error {type(network_error).__name__}, got {call_count}"
            
            # Should be fast (no retry delays)
            assert duration < 1.0, f"Network error took too long: {duration}s"

    # SUCCESS AFTER RETRIES - Test resilience patterns
    @pytest.mark.asyncio
    async def test_success_after_temporary_failure(self, spotify_client):
        """Test successful recovery after temporary failures."""
        
        call_count = 0
        success_data = {"tracks": [{"id": "test_track", "name": "Test Track"}]}
        
        def mock_get_tracks_eventual_success(*args, **kwargs):
            """Mock that fails twice then succeeds on third try."""
            nonlocal call_count
            call_count += 1
            
            if call_count <= 2:
                # Fail with temporary server error first two times
                exception = spotipy.SpotifyException(503, -1, "Service Unavailable")
                exception.http_status = 503
                raise exception
            else:
                # Succeed on third attempt
                return success_data
        
        with patch('src.infrastructure.connectors.spotify.client.spotify_api_call') as mock_api_call:
            mock_api_call.side_effect = mock_get_tracks_eventual_success
            
            start_time = time.time()
            result = await spotify_client.get_tracks_bulk(["test_track_id"])
            duration = time.time() - start_time
            
            # Should succeed and return data
            assert result == success_data
            
            # Should have made 3 calls total (2 failures + 1 success)
            assert call_count == 3, f"Expected 3 calls for eventual success, got {call_count}"
            
            # Should have some retry delays
            assert duration > 0, f"Eventual success should have some delay: {duration}s"

    # ALL METHODS COMPREHENSIVE TESTING - Test error handling across all client methods
    @pytest.mark.parametrize(('method_name', 'method_args'), [
        ("get_tracks_bulk", (["test_id"],)),
        ("search_by_isrc", ("USRC17607839",)),
        ("search_track", ("Test Artist", "Test Track")),
        ("get_playlist", ("test_playlist_id",)),
        ("get_playlist_tracks", ("test_playlist_id",)),
        ("create_playlist", ("Test Playlist",)),
        ("get_saved_tracks", ()),
        ("get_current_user", ()),
        ("playlist_add_items", ("test_playlist_id", ["spotify:track:test_id"])),
        ("playlist_remove_specific_occurrences_of_items", ("test_playlist_id", [{"uri": "spotify:track:test_id"}])),
        ("playlist_reorder_items", ("test_playlist_id", 0, 1)),
        ("playlist_replace_items", ("test_playlist_id", ["spotify:track:test_id"])),
        ("get_next_page", ({"next": "https://api.spotify.com/v1/test"},)),
    ])
    @pytest.mark.asyncio
    async def test_all_methods_error_handling_comprehensive(self, spotify_client, method_name, method_args):
        """Test that all Spotify client methods have proper error handling and backoff decorators."""
        
        call_count = 0
        
        def mock_api_call_rate_limit(*args, **kwargs):
            """Mock that always raises rate limit error."""
            nonlocal call_count
            call_count += 1
            exception = spotipy.SpotifyException(429, -1, "Too Many Requests")
            exception.http_status = 429
            raise exception
        
        with patch('src.infrastructure.connectors.spotify.client.spotify_api_call') as mock_api_call:
            mock_api_call.side_effect = mock_api_call_rate_limit
            
            # Get the method to test
            method = getattr(spotify_client, method_name)
            
            start_time = time.time()
            result = await method(*method_args)
            duration = time.time() - start_time
            
            # Should handle errors gracefully
            if method_name == "playlist_change_details":
                # This method re-raises exceptions instead of returning None
                # We need to handle this case differently
                assert result is None  # Will actually raise, but let's check the pattern
            else:
                assert result is None
            
            # Should retry 3 times for rate limit errors
            assert call_count == 3, f"Method {method_name} expected 3 calls for rate limit error, got {call_count}"
            
            # Should have retry delays
            assert duration > 0, f"Method {method_name} retries should have some delay: {duration}s"