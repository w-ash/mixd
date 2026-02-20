"""Comprehensive error classification tests for Spotify API integration.

This test suite provides exhaustive coverage of all error codes and patterns
defined in src/infrastructure/connectors/spotify/error_classifier.py.

Tests ensure that each error type triggers the correct retry behavior:
- Permanent errors: No retries (immediate failure)
- Temporary errors: 2-3 retries with exponential backoff
- Rate limit errors: 2-3 retries with constant delay
- Not found errors: No retries (immediate failure with debug logging)
- Unknown errors: 2-3 retries with exponential backoff

Injection strategy: patch individual _impl methods to inject httpx errors.
This tests the retry policy behavior without requiring real HTTP connections.
"""

import time
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from src.infrastructure.connectors.spotify.client import SpotifyAPIClient


def make_httpx_error(status_code: int, message: str = "") -> httpx.HTTPStatusError:
    """Create an httpx.HTTPStatusError with the given status code."""
    req = httpx.Request("GET", "https://api.spotify.com/v1/tracks")
    resp = httpx.Response(status_code, request=req)
    return httpx.HTTPStatusError(message or f"HTTP {status_code}", request=req, response=resp)


def make_network_error(message: str = "Connection refused") -> httpx.ConnectError:
    """Create an httpx.ConnectError (subclass of httpx.RequestError)."""
    req = httpx.Request("GET", "https://api.spotify.com/v1/tracks")
    return httpx.ConnectError(message, request=req)


@pytest.mark.slow
@pytest.mark.integration
class TestComprehensiveErrorClassification:
    """Comprehensive HTTP status code coverage testing with all Spotify API error scenarios."""

    @pytest.fixture
    def spotify_client(self):
        """Spotify client with mocked settings."""
        with patch(
            "src.infrastructure.connectors.spotify.client.settings"
        ) as mock_settings:
            mock_settings.credentials.spotify_client_id = "test_client_id"
            mock_settings.credentials.spotify_client_secret.get_secret_value.return_value = (
                "test_secret"
            )
            mock_settings.api.spotify_market = "US"
            mock_settings.api.spotify_rate_limit = 10.0
            mock_settings.api.spotify_request_timeout = 15
            yield SpotifyAPIClient()

    # PERMANENT ERRORS (4xx status codes) - Should NOT retry, immediate failure
    @pytest.mark.parametrize(
        ("status_code", "description"),
        [
            (400, "Bad Request - malformed request"),
            (401, "Unauthorized - invalid or expired token"),
            (403, "Forbidden - insufficient permissions"),
            (422, "Unprocessable Entity - request data is invalid"),
            (409, "Conflict - resource already exists"),
            (415, "Unsupported Media Type"),
            (416, "Range Not Satisfiable"),
        ],
    )
    @pytest.mark.asyncio
    async def test_permanent_http_errors_no_retry_comprehensive(
        self, spotify_client, status_code, description
    ):
        """Test all permanent HTTP status codes cause immediate failure with no retries."""
        error = make_httpx_error(status_code, description)

        mock_impl = AsyncMock(side_effect=error)
        with patch.object(SpotifyAPIClient, "_get_tracks_bulk_impl", mock_impl):
            start_time = time.time()
            result = await spotify_client.get_tracks_bulk(["test_track_id"])
            duration = time.time() - start_time

        # Should return None gracefully (no exception raised)
        assert result is None

        # Should NOT retry (only 1 call) - permanent errors are immediate failures
        assert mock_impl.call_count == 1, (
            f"Expected 1 call for permanent error {status_code}, got {mock_impl.call_count}"
        )

        # Should be fast (no retry delays)
        assert duration < 2.0, f"Permanent error took too long: {duration}s"

    # NOT FOUND ERRORS (404) - Should NOT retry, immediate failure
    @pytest.mark.asyncio
    async def test_not_found_error_no_retry(self, spotify_client):
        """Test 404 Not Found causes immediate failure with no retries."""
        error = make_httpx_error(404, "Not Found - resource doesn't exist")

        mock_impl = AsyncMock(side_effect=error)
        with patch.object(SpotifyAPIClient, "_get_tracks_bulk_impl", mock_impl):
            start_time = time.time()
            result = await spotify_client.get_tracks_bulk(["nonexistent_track_id"])
            duration = time.time() - start_time

        assert result is None
        assert mock_impl.call_count == 1, (
            f"Expected 1 call for not found error, got {mock_impl.call_count}"
        )
        assert duration < 2.0, f"Not found error took too long: {duration}s"

    # RATE LIMIT ERRORS (429) - Should retry 2-3 times with backoff
    @pytest.mark.asyncio
    async def test_rate_limit_error_retries(self, spotify_client):
        """Test 429 Too Many Requests triggers retries with proper backoff."""
        error = make_httpx_error(429, "Too Many Requests - rate limit exceeded")

        mock_impl = AsyncMock(side_effect=error)
        with patch.object(SpotifyAPIClient, "_get_tracks_bulk_impl", mock_impl):
            start_time = time.time()
            result = await spotify_client.get_tracks_bulk(["test_track_id"])
            duration = time.time() - start_time

        assert result is None
        assert mock_impl.call_count == 3, (
            f"Expected 3 calls for rate limit error, got {mock_impl.call_count}"
        )
        assert duration > 0, f"Rate limit retries should have some delay: {duration}s"

    # TEMPORARY ERRORS (5xx status codes) - Should retry 2-3 times with backoff
    @pytest.mark.parametrize(
        ("status_code", "description"),
        [
            (500, "Internal Server Error"),
            (502, "Bad Gateway - upstream server issue"),
            (503, "Service Unavailable"),
            (504, "Gateway Timeout"),
            (507, "Insufficient Storage"),
            (508, "Loop Detected"),
            (511, "Network Authentication Required"),
        ],
    )
    @pytest.mark.asyncio
    async def test_temporary_server_errors_retry_comprehensive(
        self, spotify_client, status_code, description
    ):
        """Test all temporary server error status codes trigger proper retries."""
        error = make_httpx_error(status_code, description)

        mock_impl = AsyncMock(side_effect=error)
        with patch.object(SpotifyAPIClient, "_get_tracks_bulk_impl", mock_impl):
            start_time = time.time()
            result = await spotify_client.get_tracks_bulk(["test_track_id"])
            duration = time.time() - start_time

        assert result is None
        assert mock_impl.call_count == 3, (
            f"Expected 3 calls for server error {status_code}, got {mock_impl.call_count}"
        )
        assert duration > 0, f"Server error retries should have some delay: {duration}s"

    # NETWORK ERRORS (httpx.RequestError) - Retried as temporary (not propagated)
    @pytest.mark.parametrize(
        "error_message",
        [
            "Connection failed to Spotify API",
            "Request timeout after 30 seconds",
            "DNS resolution failed for api.spotify.com",
        ],
    )
    @pytest.mark.asyncio
    async def test_network_errors_retried_as_temporary(
        self, spotify_client, error_message
    ):
        """Test httpx network errors (RequestError) are retried 3 times as temporary.

        Unlike the old spotipy-based implementation where non-SpotifyException errors
        propagated immediately, httpx.RequestError is explicitly in the retry type filter
        and is classified as 'temporary' — so it gets 3 retry attempts before returning None.
        """
        req = httpx.Request("GET", "https://api.spotify.com/v1/tracks")
        error = httpx.ConnectError(error_message, request=req)

        mock_impl = AsyncMock(side_effect=error)
        with patch.object(SpotifyAPIClient, "_get_tracks_bulk_impl", mock_impl):
            result = await spotify_client.get_tracks_bulk(["test_track_id"])

        # Network errors ARE retried (3 times) and then return None
        assert result is None
        assert mock_impl.call_count == 3, (
            f"Expected 3 retries for network error, got {mock_impl.call_count}"
        )

    # SUCCESS AFTER RETRIES - Test resilience patterns
    @pytest.mark.asyncio
    async def test_success_after_temporary_failure(self, spotify_client):
        """Test successful recovery after temporary failures."""
        success_data = {"tracks": [{"id": "test_track", "name": "Test Track"}]}
        error = make_httpx_error(503, "Service Unavailable")

        mock_impl = AsyncMock(side_effect=[error, error, success_data])
        with patch.object(SpotifyAPIClient, "_get_tracks_bulk_impl", mock_impl):
            start_time = time.time()
            result = await spotify_client.get_tracks_bulk(["test_track_id"])
            duration = time.time() - start_time

        # Should succeed and return data on 3rd attempt
        assert result == success_data
        assert mock_impl.call_count == 3, (
            f"Expected 3 calls for eventual success, got {mock_impl.call_count}"
        )
        assert duration > 0, f"Eventual success should have some delay: {duration}s"

    # ALL METHODS COMPREHENSIVE TESTING - Test error handling across all client methods
    @pytest.mark.parametrize(
        ("method_name", "method_args", "impl_name"),
        [
            ("get_tracks_bulk", (["test_id"],), "_get_tracks_bulk_impl"),
            ("search_by_isrc", ("USRC17607839",), "_search_by_isrc_impl"),
            ("search_track", ("Test Artist", "Test Track"), "_search_track_impl"),
            ("get_playlist", ("test_playlist_id",), "_get_playlist_impl"),
            ("get_playlist_tracks", ("test_playlist_id",), "_get_playlist_tracks_impl"),
            ("create_playlist", ("Test Playlist",), "_create_playlist_impl"),
            ("get_saved_tracks", (), "_get_saved_tracks_impl"),
            ("get_current_user", (), "_get_current_user_impl"),
            (
                "playlist_add_items",
                ("test_playlist_id", ["spotify:track:test_id"]),
                "_playlist_add_items_impl",
            ),
            (
                "playlist_remove_specific_occurrences_of_items",
                ("test_playlist_id", [{"uri": "spotify:track:test_id"}]),
                "_playlist_remove_specific_occurrences_of_items_impl",
            ),
            (
                "playlist_reorder_items",
                ("test_playlist_id", 0, 1),
                "_playlist_reorder_items_impl",
            ),
            (
                "playlist_replace_items",
                ("test_playlist_id", ["spotify:track:test_id"]),
                "_playlist_replace_items_impl",
            ),
            (
                "get_next_page",
                ({"next": "https://api.spotify.com/v1/test"},),
                "_get_next_page_impl",
            ),
        ],
    )
    @pytest.mark.asyncio
    async def test_all_methods_error_handling_comprehensive(
        self, spotify_client, method_name, method_args, impl_name
    ):
        """Test that all Spotify client methods have proper error handling and retry behavior."""
        error = make_httpx_error(429, "Too Many Requests")

        mock_impl = AsyncMock(side_effect=error)
        with patch.object(SpotifyAPIClient, impl_name, mock_impl):
            method = getattr(spotify_client, method_name)
            start_time = time.time()
            result = await method(*method_args)
            duration = time.time() - start_time

        assert result is None

        # Should retry 3 times for rate limit errors
        assert mock_impl.call_count == 3, (
            f"Method {method_name} expected 3 calls for rate limit error, "
            f"got {mock_impl.call_count}"
        )

        assert duration > 0, f"Method {method_name} retries should have some delay: {duration}s"
