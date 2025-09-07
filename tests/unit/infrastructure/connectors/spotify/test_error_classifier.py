"""Unit tests for SpotifyErrorClassifier.

This test suite provides comprehensive coverage of the error classification logic
in src/infrastructure/connectors/spotify/error_classifier.py without requiring
actual API calls or network connectivity.

Tests focus on the pure classification logic for all error types:
- HTTP status code mapping (4xx → permanent, 5xx → temporary, 429 → rate_limit, 404 → not_found)
- Text pattern recognition (rate limit, auth, not found, service issues)
- Non-Spotify exception handling (network errors, etc.)
- Edge cases and unknown errors
"""

import pytest
import spotipy

from src.infrastructure.connectors.spotify.error_classifier import (
    SpotifyErrorClassifier,
)


class TestSpotifyErrorClassifier:
    """Unit tests for SpotifyErrorClassifier classification logic."""

    @pytest.fixture
    def classifier(self):
        """Create a SpotifyErrorClassifier instance."""
        return SpotifyErrorClassifier()

    # HTTP STATUS CODE CLASSIFICATION TESTS
    
    @pytest.mark.parametrize(("status_code", "expected_type", "expected_description_part"), [
        # Client errors (4xx) - permanent
        (400, "permanent", "Bad Request"),
        (401, "permanent", "Unauthorized"),
        (403, "permanent", "Forbidden"),
        (409, "permanent", "Client error"),
        (422, "permanent", "Client error"),
        (415, "permanent", "Client error"),
        (416, "permanent", "Client error"),
        (418, "permanent", "Client error"),  # I'm a teapot - should be permanent
    ])
    def test_client_error_status_codes_permanent(self, classifier, status_code, expected_type, expected_description_part):
        """Test that 4xx HTTP status codes are classified as permanent errors."""
        exception = spotipy.SpotifyException(status_code, -1, f"HTTP {status_code} error")
        exception.http_status = status_code
        
        error_type, error_code, error_description = classifier.classify_error(exception)
        
        assert error_type == expected_type
        assert error_code == str(status_code)
        assert expected_description_part.lower() in error_description.lower()

    def test_not_found_status_code_specific(self, classifier):
        """Test that 404 HTTP status code is specifically classified as not_found."""
        exception = spotipy.SpotifyException(404, -1, "Not Found")
        exception.http_status = 404
        
        error_type, error_code, error_description = classifier.classify_error(exception)
        
        assert error_type == "not_found"
        assert error_code == "404"
        assert "not found" in error_description.lower()

    def test_rate_limit_status_code_specific(self, classifier):
        """Test that 429 HTTP status code is specifically classified as rate_limit."""
        exception = spotipy.SpotifyException(429, -1, "Too Many Requests")
        exception.http_status = 429
        
        error_type, error_code, error_description = classifier.classify_error(exception)
        
        assert error_type == "rate_limit"
        assert error_code == "429"
        assert "rate limit" in error_description.lower()

    @pytest.mark.parametrize(("status_code", "expected_type", "expected_description_part"), [
        # Server errors (5xx) - temporary
        (500, "temporary", "Internal Server Error"),
        (502, "temporary", "Bad Gateway"),
        (503, "temporary", "Service Unavailable"),
        (504, "temporary", "Gateway Timeout"),
        (507, "temporary", "Server error"),
        (508, "temporary", "Server error"),
        (511, "temporary", "Server error"),
        (599, "temporary", "Server error"),  # Edge case high 5xx
    ])
    def test_server_error_status_codes_temporary(self, classifier, status_code, expected_type, expected_description_part):
        """Test that 5xx HTTP status codes are classified as temporary errors."""
        exception = spotipy.SpotifyException(status_code, -1, f"HTTP {status_code} error")
        exception.http_status = status_code
        
        error_type, error_code, error_description = classifier.classify_error(exception)
        
        assert error_type == expected_type
        assert error_code == str(status_code)
        assert expected_description_part.lower() in error_description.lower()

    # TEXT PATTERN CLASSIFICATION TESTS

    @pytest.mark.parametrize("error_message", [
        "Rate limit exceeded",
        "Too many requests",
        "Quota exceeded", 
        "Request throttled",
        "API rate limit hit",
    ])
    def test_rate_limit_text_patterns(self, classifier, error_message):
        """Test that rate limit text patterns are classified correctly."""
        # Create exception without http_status to test text-based classification
        exception = spotipy.SpotifyException(-1, -1, error_message)
        
        error_type, error_code, error_description = classifier.classify_error(exception)
        
        assert error_type == "rate_limit"
        assert error_code == "text"
        assert "rate limit" in error_description.lower()

    @pytest.mark.parametrize("error_message", [
        "invalid access token",
        "token expired", 
        "unauthorized request",
        "invalid_grant error",
        "invalid_client provided",
        "access_denied by user",
    ])
    def test_authentication_text_patterns(self, classifier, error_message):
        """Test that authentication error text patterns are classified as permanent."""
        exception = spotipy.SpotifyException(-1, -1, error_message)
        
        error_type, error_code, error_description = classifier.classify_error(exception)
        
        assert error_type == "permanent"
        assert error_code == "auth"
        assert "authentication" in error_description.lower() or "authorization" in error_description.lower()

    @pytest.mark.parametrize("error_message", [
        "Track not found",
        "Playlist does not exist",
        "No such resource", 
        "invalid id provided",
        "Artist not found in database",
    ])
    def test_not_found_text_patterns(self, classifier, error_message):
        """Test that not found text patterns are classified correctly."""
        exception = spotipy.SpotifyException(-1, -1, error_message)
        
        error_type, error_code, error_description = classifier.classify_error(exception)
        
        assert error_type == "not_found"
        assert error_code == "text"
        assert "not found" in error_description.lower()

    @pytest.mark.parametrize("error_message", [
        "Service temporarily unavailable",
        "Internal server error occurred",
        "Please try again later",
        "Service is temporarily down",
        "System unavailable for maintenance",
        "Internal error - please retry",
    ])
    def test_temporary_service_text_patterns(self, classifier, error_message):
        """Test that temporary service error text patterns are classified correctly."""
        exception = spotipy.SpotifyException(-1, -1, error_message)
        
        error_type, error_code, error_description = classifier.classify_error(exception)
        
        assert error_type == "temporary"
        assert error_code == "text"
        assert "temporarily" in error_description.lower() or "unavailable" in error_description.lower()

    # NON-SPOTIFY EXCEPTION TESTS

    @pytest.mark.parametrize(("exception_type", "exception_message"), [
        (ConnectionError, "Connection failed to Spotify API"),
        (TimeoutError, "Request timeout after 30 seconds"), 
        (OSError, "Network is unreachable"),
        (Exception, "DNS resolution failed for api.spotify.com"),
        (Exception, "SSL certificate verification failed"),
    ])
    def test_network_errors_temporary_classification(self, classifier, exception_type, exception_message):
        """Test that network errors are classified as temporary."""
        exception = exception_type(exception_message)
        
        error_type, error_code, error_description = classifier.classify_error(exception)
        
        assert error_type == "temporary"
        assert error_code == "network"
        assert "network" in error_description.lower() or "connection" in error_description.lower()

    def test_non_network_non_spotify_exception_unknown(self, classifier):
        """Test that unknown non-Spotify exceptions are classified as unknown."""
        exception = ValueError("Unexpected value error")
        
        error_type, error_code, error_description = classifier.classify_error(exception)
        
        assert error_type == "unknown"
        assert error_code == "N/A"
        assert error_description == str(exception)

    # SPOTIFY ERROR DETAILS PARSING TESTS

    def test_parse_spotify_oauth_error_details(self, classifier):
        """Test parsing of OAuth error details from Spotify error messages."""
        error_msg = "error: invalid_grant, error_description: Authorization code expired"
        exception = spotipy.SpotifyException(400, -1, error_msg)
        
        details = classifier._parse_spotify_error_details(exception)
        
        assert details.get("error") == "invalid_grant"
        assert "Authorization code expired" in details.get("error_description", "")

    def test_parse_spotify_simple_error_details(self, classifier):
        """Test parsing simple error patterns from Spotify error messages."""
        error_msg = "HTTP 401: error: invalid_token"
        exception = spotipy.SpotifyException(401, -1, error_msg)
        
        details = classifier._parse_spotify_error_details(exception)
        
        assert details.get("error") == "invalid_token"

    def test_parse_spotify_error_details_malformed(self, classifier):
        """Test graceful handling of malformed Spotify error messages."""
        error_msg = "Some random error message without proper formatting"
        exception = spotipy.SpotifyException(500, -1, error_msg)
        
        details = classifier._parse_spotify_error_details(exception)
        
        # Should return empty dict for unparseable messages
        assert details == {}

    def test_parse_spotify_error_details_exception_safety(self, classifier):
        """Test that error details parsing doesn't raise exceptions."""
        # Create a mock exception that might cause parsing errors
        class ProblematicException(spotipy.SpotifyException):
            def __str__(self):
                raise RuntimeError("String conversion failed")
        
        exception = ProblematicException(500, -1, "test")
        
        # Should not raise an exception, should return empty dict
        details = classifier._parse_spotify_error_details(exception)
        assert details == {}

    # EDGE CASES AND UNKNOWN ERRORS

    def test_spotify_exception_without_http_status_unknown(self, classifier):
        """Test SpotifyException without http_status falls back to text classification."""
        exception = spotipy.SpotifyException(-1, -1, "Mysterious Spotify error")
        
        error_type, _error_code, error_description = classifier.classify_error(exception)
        
        assert error_type == "unknown"
        assert "Mysterious Spotify error" in error_description

    def test_spotify_exception_with_none_http_status(self, classifier):
        """Test SpotifyException with None http_status."""
        exception = spotipy.SpotifyException(-1, -1, "Error with None status")
        exception.http_status = None
        
        error_type, _error_code, error_description = classifier.classify_error(exception)
        
        assert error_type == "unknown"
        assert "Error with None status" in error_description

    def test_unknown_http_status_code(self, classifier):
        """Test handling of unknown HTTP status codes."""
        exception = spotipy.SpotifyException(999, -1, "Unknown HTTP status")
        exception.http_status = 999
        
        error_type, error_code, _error_description = classifier.classify_error(exception)
        
        assert error_type == "unknown"
        assert error_code == "999"  # Uses HTTP status as error code even for unknown statuses

    # INTEGRATION WITH ERROR DETAILS PARSING

    def test_error_classification_with_parsed_details(self, classifier):
        """Test that error classification uses parsed error details when available."""
        error_msg = "HTTP 401: error: expired_token, error_description: The access token has expired"
        exception = spotipy.SpotifyException(401, -1, error_msg)
        exception.http_status = 401
        
        error_type, error_code, error_description = classifier.classify_error(exception)
        
        assert error_type == "permanent"
        assert error_code == "401"  # Uses HTTP status as error code
        assert "unauthorized" in error_description.lower()
        # Note: The parsed error_description is used but overridden by status code description

    def test_error_code_fallback_logic(self, classifier):
        """Test the error code fallback logic in classification."""
        # Test with parsed error details
        error_msg = "HTTP 500: error: internal_server_error"
        exception = spotipy.SpotifyException(500, -1, error_msg)
        exception.http_status = 500
        
        error_type, error_code, _error_description = classifier.classify_error(exception)
        
        assert error_type == "temporary"
        assert error_code == "500"  # Uses HTTP status as error code
        
        # Test without HTTP status (should use parsed error or "unknown")
        exception2 = spotipy.SpotifyException(-1, -1, error_msg)
        error_type2, error_code2, _error_description2 = classifier.classify_error(exception2)
        
        assert error_type2 == "unknown"
        assert error_code2 == "internal_server_error"  # Uses parsed error as error code