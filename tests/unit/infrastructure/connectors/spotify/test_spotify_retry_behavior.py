"""Spotify retry behavior tests — classifier + tenacity wiring.

Tests that the error classifier decisions flow correctly through the tenacity
retry policy into each client method. Covers:
- Permanent errors (4xx): No retries, immediate failure
- Temporary errors (5xx): 3 retries with exponential backoff
- Rate limit errors (429): 3 retries with constant delay
- Not found errors (404): No retries, immediate failure
- Network errors: 3 retries as temporary
- Recovery: Success after transient failures
- Every client method wired with retry

Injection strategy: the per-classification cases stub the pooled HTTP
transport (``client._client.get``) with real ``httpx2.Response`` objects, so
each attempt runs the client's own request code and ``raise_for_status``. They
all run through ``get_tracks_batched``; a suppressed failure surfaces as the
chunk's ids in ``unanswered`` — the shape that keeps a failed request from
being read as a dead id. Every other method routes its HTTP through ``_json``,
whose per-attempt unit is ``_request_json`` — the single injection point for
the all-methods sweep.
"""

from unittest.mock import AsyncMock, patch

import httpx2
import pytest

from src.infrastructure.connectors.spotify.client import SpotifyAPIClient
from src.infrastructure.connectors.spotify.models import SpotifyPaginatedPlaylistItems


def make_httpx_error(status_code: int, message: str = "") -> httpx2.HTTPStatusError:
    """Create an httpx2.HTTPStatusError with the given status code."""
    req = httpx2.Request("GET", "https://api.spotify.com/v1/tracks")
    resp = httpx2.Response(status_code, request=req)
    return httpx2.HTTPStatusError(
        message or f"HTTP {status_code}", request=req, response=resp
    )


def make_network_error(message: str = "Connection refused") -> httpx2.ConnectError:
    """Create an httpx2.ConnectError (subclass of httpx2.RequestError)."""
    req = httpx2.Request("GET", "https://api.spotify.com/v1/tracks")
    return httpx2.ConnectError(message, request=req)


def _response(status_code: int, json_body: object = None) -> httpx2.Response:
    """A real response to GET /tracks, so ``raise_for_status`` behaves as live."""
    req = httpx2.Request("GET", "https://api.spotify.com/v1/tracks")
    return httpx2.Response(status_code, json=json_body, request=req)


def _stub_transport(client: SpotifyAPIClient, **get_behaviour: object) -> AsyncMock:
    """Replace the pooled HTTP client; ``get`` answers per ``get_behaviour``."""
    transport = AsyncMock()
    transport.get = AsyncMock(**get_behaviour)
    client._client = transport
    return transport


@pytest.mark.slow
class TestComprehensiveErrorClassification:
    """Comprehensive HTTP status code coverage testing with all Spotify API error scenarios."""

    @pytest.fixture
    def spotify_client(self):
        """Spotify client with mocked settings."""
        with patch(
            "src.infrastructure.connectors.spotify.client.settings"
        ) as mock_settings:
            mock_settings.credentials.spotify_client_id = "test_client_id"
            mock_settings.credentials.spotify_client_secret.get_secret_value.return_value = "test_secret"
            mock_settings.api.spotify_market = "US"
            mock_settings.api.spotify.rate_limit = 10.0
            mock_settings.api.spotify.request_timeout = 15
            # Concrete: the batch fetch sizes an asyncio.Semaphore with it.
            mock_settings.api.spotify.concurrency = 4
            # Retry policy parameters — must be concrete values, not MagicMock
            mock_settings.api.spotify.retry_count = 3
            mock_settings.api.spotify.retry_base_delay = 0.5
            mock_settings.api.spotify.retry_max_delay = 30.0
            yield SpotifyAPIClient()

    @pytest.fixture
    def fast_retry_client(self, spotify_client):
        """Client with instant retries — no exponential backoff waits."""
        from tenacity import wait_none

        spotify_client._retry_policy.wait = wait_none()
        return spotify_client

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
    async def test_permanent_http_errors_no_retry_comprehensive(
        self, spotify_client, status_code, description
    ):
        """Test all permanent HTTP status codes cause immediate failure with no retries."""
        del description
        transport = _stub_transport(spotify_client, return_value=_response(status_code))

        fetch = await spotify_client.get_tracks_batched(["test_track_id"])

        # Should degrade gracefully to "no answer" (no exception raised)
        assert fetch.tracks == {}
        assert fetch.unanswered == frozenset({"test_track_id"})

        # Should NOT retry (only 1 call) - permanent errors are immediate failures
        assert transport.get.await_count == 1

    # NOT FOUND ERRORS (404) - Should NOT retry, immediate failure
    async def test_not_found_error_no_retry(self, spotify_client):
        """Test 404 Not Found causes immediate failure with no retries."""
        transport = _stub_transport(spotify_client, return_value=_response(404))

        fetch = await spotify_client.get_tracks_batched(["nonexistent_track_id"])

        assert fetch.tracks == {}
        assert fetch.unanswered == frozenset({"nonexistent_track_id"})
        assert transport.get.await_count == 1

    # RATE LIMIT ERRORS (429) - Should retry 2-3 times with backoff
    async def test_rate_limit_error_retries(self, fast_retry_client):
        """Test 429 Too Many Requests triggers retries with proper backoff."""
        transport = _stub_transport(fast_retry_client, return_value=_response(429))

        fetch = await fast_retry_client.get_tracks_batched(["test_track_id"])

        assert fetch.unanswered == frozenset({"test_track_id"})
        assert transport.get.await_count == 3

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
    async def test_temporary_server_errors_retry_comprehensive(
        self, fast_retry_client, status_code, description
    ):
        """Test all temporary server error status codes trigger proper retries."""
        del description
        transport = _stub_transport(
            fast_retry_client, return_value=_response(status_code)
        )

        fetch = await fast_retry_client.get_tracks_batched(["test_track_id"])

        assert fetch.unanswered == frozenset({"test_track_id"})
        assert transport.get.await_count == 3

    # NETWORK ERRORS (httpx2.RequestError) - Retried as temporary (not propagated)
    @pytest.mark.parametrize(
        "error_message",
        [
            "Connection failed to Spotify API",
            "Request timeout after 30 seconds",
            "DNS resolution failed for api.spotify.com",
        ],
    )
    async def test_network_errors_retried_as_temporary(
        self, fast_retry_client, error_message
    ):
        """Test httpx2 network errors (RequestError) are retried 3 times as temporary.

        Unlike the old spotipy-based implementation where non-SpotifyException errors
        propagated immediately, httpx2.RequestError is explicitly in the retry type filter
        and is classified as 'temporary' — so it gets 3 retry attempts before the
        chunk's ids are reported unanswered.
        """
        transport = _stub_transport(
            fast_retry_client, side_effect=make_network_error(error_message)
        )

        fetch = await fast_retry_client.get_tracks_batched(["test_track_id"])

        # Network errors ARE retried (3 times) and then leave the id unanswered
        assert fetch.unanswered == frozenset({"test_track_id"})
        assert transport.get.await_count == 3

    # SUCCESS AFTER RETRIES - Test resilience patterns
    async def test_success_after_temporary_failure(self, fast_retry_client):
        """Test successful recovery after temporary failures."""
        success = _response(
            200, {"tracks": [{"id": "test_track_id", "name": "Test Track"}]}
        )
        transport = _stub_transport(
            fast_retry_client, side_effect=[_response(503), _response(503), success]
        )

        fetch = await fast_retry_client.get_tracks_batched(["test_track_id"])

        # Should succeed and return the parsed model on the 3rd attempt
        assert fetch.unanswered == frozenset()
        assert fetch.tracks["test_track_id"].id == "test_track_id"
        assert transport.get.await_count == 3

    # ALL METHODS COMPREHENSIVE TESTING - Test error handling across all client methods
    @pytest.mark.parametrize(
        ("method_name", "method_args"),
        [
            # get_tracks_batched is absent by design: its result is a
            # SpotifyTracksFetch, not a falsy None, and the classification
            # tests above already drive every error class through it.
            ("search_by_isrc", ("USRC17607839",)),
            (
                "search_track",
                ('artist:"Test Artist" track:"Test Track"',),
            ),
            ("get_playlist", ("test_playlist_id",)),
            ("create_playlist", ("Test Playlist",)),
            ("get_saved_tracks", ()),
            ("get_current_user", ()),
            (
                "playlist_add_items",
                ("test_playlist_id", ["spotify:track:test_id"]),
            ),
            (
                "playlist_remove_specific_occurrences_of_items",
                ("test_playlist_id", [{"uri": "spotify:track:test_id"}]),
            ),
            (
                "playlist_reorder_items",
                ("test_playlist_id", 0, 1),
            ),
            (
                "playlist_replace_items",
                ("test_playlist_id", ["spotify:track:test_id"]),
            ),
            (
                "get_next_page",
                (
                    SpotifyPaginatedPlaylistItems(
                        next="https://api.spotify.com/v1/test"
                    ),
                ),
            ),
        ],
    )
    async def test_all_methods_error_handling_comprehensive(
        self, fast_retry_client, method_name, method_args
    ):
        """Test that all Spotify client methods have proper error handling and retry behavior."""
        error = make_httpx_error(429, "Too Many Requests")

        mock_request = AsyncMock(side_effect=error)
        with patch.object(SpotifyAPIClient, "_request_json", mock_request):
            method = getattr(fast_retry_client, method_name)
            result = await method(*method_args)

        # The suppressed failure's empty value: [] for search_track, else None.
        assert result == ([] if method_name == "search_track" else None)

        # Should retry 3 times for rate limit errors
        assert mock_request.call_count == 3, (
            f"Method {method_name} expected 3 calls for rate limit error, "
            f"got {mock_request.call_count}"
        )
