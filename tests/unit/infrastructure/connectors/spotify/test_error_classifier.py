"""Unit tests for SpotifyErrorClassifier.

Tests focus on the pure classification logic using httpx exceptions:
- HTTP status code mapping (4xx → permanent, 5xx → temporary, 429 → rate_limit, 404 → not_found)
- Text pattern recognition (rate limit, auth, not found, service issues)
- httpx.RequestError handling (network errors → temporary)
- Edge cases and unknown errors
"""

import httpx
import pytest

from src.infrastructure.connectors.spotify.error_classifier import (
    SpotifyErrorClassifier,
)


def make_http_error(status_code: int, message: str = "") -> httpx.HTTPStatusError:
    """Create an httpx.HTTPStatusError with the given status code."""
    request = httpx.Request("GET", "https://api.spotify.com/v1/tracks")
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError(
        message or f"HTTP {status_code}", request=request, response=response
    )


class TestSpotifyErrorClassifier:
    """Unit tests for SpotifyErrorClassifier classification logic."""

    @pytest.fixture
    def classifier(self):
        """Create a SpotifyErrorClassifier instance."""
        return SpotifyErrorClassifier()

    # HTTP STATUS CODE CLASSIFICATION TESTS

    @pytest.mark.parametrize(
        ("status_code", "expected_type", "expected_description_part"),
        [
            # Client errors (4xx) - permanent
            (400, "permanent", "Bad Request"),
            (401, "permanent", "Unauthorized"),
            (403, "permanent", "Forbidden"),
            (409, "permanent", "Client error"),
            (422, "permanent", "Client error"),
            (415, "permanent", "Client error"),
            (416, "permanent", "Client error"),
            (418, "permanent", "Client error"),  # I'm a teapot - should be permanent
        ],
    )
    def test_client_error_status_codes_permanent(
        self, classifier, status_code, expected_type, expected_description_part
    ):
        """Test that 4xx HTTP status codes are classified as permanent errors."""
        exception = make_http_error(status_code)

        error_type, error_code, error_description = classifier.classify_error(exception)

        assert error_type == expected_type
        assert error_code == str(status_code)
        assert expected_description_part.lower() in error_description.lower()

    def test_not_found_status_code_specific(self, classifier):
        """Test that 404 HTTP status code is specifically classified as not_found."""
        exception = make_http_error(404, "Not Found")

        error_type, error_code, error_description = classifier.classify_error(exception)

        assert error_type == "not_found"
        assert error_code == "404"
        assert "not found" in error_description.lower()

    def test_rate_limit_status_code_specific(self, classifier):
        """Test that 429 HTTP status code is specifically classified as rate_limit."""
        exception = make_http_error(429, "Too Many Requests")

        error_type, error_code, error_description = classifier.classify_error(exception)

        assert error_type == "rate_limit"
        assert error_code == "429"
        assert "rate limit" in error_description.lower()

    @pytest.mark.parametrize(
        ("status_code", "expected_type", "expected_description_part"),
        [
            # Server errors (5xx) - temporary
            (500, "temporary", "Internal Server Error"),
            (502, "temporary", "Bad Gateway"),
            (503, "temporary", "Service Unavailable"),
            (504, "temporary", "Gateway Timeout"),
            (507, "temporary", "Server error"),
            (508, "temporary", "Server error"),
            (511, "temporary", "Server error"),
            (599, "temporary", "Server error"),  # Edge case high 5xx
        ],
    )
    def test_server_error_status_codes_temporary(
        self, classifier, status_code, expected_type, expected_description_part
    ):
        """Test that 5xx HTTP status codes are classified as temporary errors."""
        exception = make_http_error(status_code)

        error_type, error_code, error_description = classifier.classify_error(exception)

        assert error_type == expected_type
        assert error_code == str(status_code)
        assert expected_description_part.lower() in error_description.lower()

    # TEXT PATTERN CLASSIFICATION TESTS (via non-httpx exceptions)

    @pytest.mark.parametrize(
        "error_message",
        [
            "Rate limit exceeded",
            "Too many requests",
            "Quota exceeded",
            "Request throttled",
            "API rate limit hit",
        ],
    )
    def test_rate_limit_text_patterns(self, classifier, error_message):
        """Test that rate limit text patterns are classified correctly."""
        exception = Exception(error_message)

        error_type, error_code, error_description = classifier.classify_error(exception)

        assert error_type == "rate_limit"
        assert error_code == "text"
        assert "rate limit" in error_description.lower()

    @pytest.mark.parametrize(
        "error_message",
        [
            "invalid access token",
            "token expired",
            "unauthorized request",
            "invalid_grant error",
            "invalid_client provided",
            "access_denied by user",
        ],
    )
    def test_authentication_text_patterns(self, classifier, error_message):
        """Test that authentication error text patterns are classified as permanent."""
        exception = Exception(error_message)

        error_type, error_code, error_description = classifier.classify_error(exception)

        assert error_type == "permanent"
        assert error_code == "auth"
        assert (
            "authentication" in error_description.lower()
            or "authorization" in error_description.lower()
        )

    @pytest.mark.parametrize(
        "error_message",
        [
            "Track not found",
            "Playlist does not exist",
            "No such resource",
            "invalid id provided",
            "Artist not found in database",
        ],
    )
    def test_not_found_text_patterns(self, classifier, error_message):
        """Test that not found text patterns are classified correctly."""
        exception = Exception(error_message)

        error_type, error_code, error_description = classifier.classify_error(exception)

        assert error_type == "not_found"
        assert error_code == "text"
        assert "not found" in error_description.lower()

    @pytest.mark.parametrize(
        "error_message",
        [
            "Service temporarily unavailable",
            "Internal server error occurred",
            "Please try again later",
            "Service is temporarily down",
            "System unavailable for maintenance",
            "Internal error - please retry",
        ],
    )
    def test_temporary_service_text_patterns(self, classifier, error_message):
        """Test that temporary service error text patterns are classified correctly."""
        exception = Exception(error_message)

        error_type, error_code, error_description = classifier.classify_error(exception)

        assert error_type == "temporary"
        assert error_code == "text"
        assert (
            "temporarily" in error_description.lower()
            or "unavailable" in error_description.lower()
        )

    # httpx.RequestError TESTS (network errors)

    @pytest.mark.parametrize(
        "error_message",
        [
            "Connection failed to Spotify API",
            "Request timeout after 30 seconds",
            "Network is unreachable",
            "DNS resolution failed for api.spotify.com",
            "SSL certificate verification failed",
        ],
    )
    def test_httpx_request_errors_temporary(self, classifier, error_message):
        """Test that httpx.RequestError instances are classified as temporary network errors."""
        request = httpx.Request("GET", "https://api.spotify.com/v1/tracks")
        exception = httpx.ConnectError(error_message, request=request)

        error_type, error_code, error_description = classifier.classify_error(exception)

        assert error_type == "temporary"
        assert error_code == "network"

    def test_non_network_exception_unknown(self, classifier):
        """Test that unknown non-httpx exceptions are classified as unknown."""
        exception = ValueError("Unexpected value error")

        error_type, error_code, error_description = classifier.classify_error(exception)

        assert error_type == "unknown"
        assert error_code == "N/A"
        assert error_description == str(exception)

    # EDGE CASES AND UNKNOWN ERRORS

    def test_unknown_http_status_code(self, classifier):
        """Test handling of unknown HTTP status codes."""
        exception = make_http_error(999, "Unknown HTTP status")

        error_type, error_code, _error_description = classifier.classify_error(
            exception
        )

        assert error_type == "unknown"
        assert error_code == "999"

    def test_error_code_fallback_logic(self, classifier):
        """Test the error code fallback logic in classification."""
        # 500 → temporary
        exception = make_http_error(500, "Internal Server Error")
        error_type, error_code, _ = classifier.classify_error(exception)

        assert error_type == "temporary"
        assert error_code == "500"

    def test_service_name(self, classifier):
        """Test that service name is correctly reported."""
        assert classifier.service_name == "spotify"
