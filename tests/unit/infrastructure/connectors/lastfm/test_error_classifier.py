"""Unit tests for LastFM error classification logic.

These are pure unit tests that test the error classification logic in isolation,
without any network calls, API clients, or complex mocking.
"""

import pylast
import pytest

from src.infrastructure.connectors.lastfm.error_classifier import LastFMErrorClassifier


class TestLastFMErrorClassifier:
    """Unit tests for LastFM error classification - no network calls, pure logic testing."""

    @pytest.fixture
    def classifier(self):
        """Error classifier instance for testing."""
        return LastFMErrorClassifier()

    # PERMANENT ERRORS - Should return "permanent" with 1 retry
    @pytest.mark.parametrize(
        "error_code",
        [
            "2",
            "3",
            "4",
            "5",
            "6",
            "7",
            "10",
            "12",
            "13",
            "14",
            "15",
            "17",
            "18",
            "21",
            "22",
            "23",
            "24",
            "25",
            "26",
            "27",
        ],
    )
    def test_permanent_error_codes(self, classifier, error_code):
        """Test all permanent error codes are classified correctly."""
        exception = pylast.WSError("LastFm", error_code, "Test error message")

        error_type, returned_code, description = classifier.classify_error(exception)

        assert error_type == "permanent"
        assert returned_code == error_code
        assert description.startswith((
            "Invalid",
            "Authentication",
            "Unauthorized",
            "Subscribers",
            "Login",
            "Trial",
            "Not Enough",
            "No Peak",
            "Radio",
            "API Key",
            "Deprecated",
            "This",
        ))

    # TEMPORARY ERRORS - Should return "temporary" with 5 retries
    @pytest.mark.parametrize("error_code", ["8", "9", "11", "16", "20"])
    def test_temporary_error_codes(self, classifier, error_code):
        """Test all temporary error codes are classified correctly."""
        exception = pylast.WSError("LastFm", error_code, "Test error message")

        error_type, returned_code, description = classifier.classify_error(exception)

        assert error_type == "temporary"
        assert returned_code == error_code
        assert description.startswith((
            "Operation failed",
            "Invalid session",
            "Service",
            "The service",
            "Not Enough Content",
        ))

    # RATE LIMIT ERRORS - Should return "rate_limit" with 8 retries
    def test_rate_limit_error_code_29(self, classifier):
        """Test error code 29 is classified as rate_limit."""
        exception = pylast.WSError("LastFm", "29", "Rate Limit Exceeded")

        error_type, returned_code, description = classifier.classify_error(exception)

        assert error_type == "rate_limit"
        assert returned_code == "29"
        assert "Rate Limit Exceded" in description

    @pytest.mark.parametrize(
        "text_pattern",
        [
            "rate limit exceeded",
            "too many requests",
            "quota exceeded",
            "throttle active",
        ],
    )
    def test_rate_limit_text_patterns(self, classifier, text_pattern):
        """Test rate limit text patterns are detected correctly."""
        exception = pylast.WSError("LastFm", "999", f"Error: {text_pattern}")

        error_type, returned_code, description = classifier.classify_error(exception)

        assert error_type == "rate_limit"
        assert returned_code == "29"
        assert "Rate Limit Exceded" in description

    # CRITICAL EDGE CASE: Error code 6 + rate limit text
    def test_error_code_6_with_rate_limit_text_is_permanent(self, classifier):
        """Test that error code 6 + 'rate limit' text returns permanent (code precedence)."""
        exception = pylast.WSError(
            "LastFm", "6", "Invalid parameters - rate limit exceeded"
        )

        error_type, returned_code, description = classifier.classify_error(exception)

        # Error CODE takes precedence over text patterns
        assert error_type == "permanent"
        assert returned_code == "6"
        assert "Invalid parameters" in description

    # NOT FOUND ERRORS - Should return "not_found" with 1 retry
    @pytest.mark.parametrize(
        "text_pattern", ["track not found", "does not exist", "no such track"]
    )
    def test_not_found_text_patterns(self, classifier, text_pattern):
        """Test not found text patterns are detected correctly."""
        exception = pylast.WSError("LastFm", "999", f"Error: {text_pattern}")

        error_type, returned_code, description = classifier.classify_error(exception)

        assert error_type == "not_found"
        assert returned_code == "N/A"
        assert description == "Resource not found"

    # NETWORK ERRORS - Should return "temporary"
    @pytest.mark.parametrize(
        "text_pattern",
        [
            "timeout",
            "connection failed",
            "network error",
            "server error",
            "503",
            "502",
            "500",
        ],
    )
    def test_network_error_text_patterns(self, classifier, text_pattern):
        """Test network error text patterns are classified as temporary."""
        exception = pylast.WSError("LastFm", "999", f"Error: {text_pattern}")

        error_type, returned_code, description = classifier.classify_error(exception)

        assert error_type == "temporary"
        assert returned_code == "text"
        assert "Network or server error" in description

    # AUTH ERRORS - Should return "permanent"
    @pytest.mark.parametrize(
        "text_pattern",
        [
            "unauthorized",
            "forbidden",
            "invalid key",
            "invalid api key",
            "authentication failed",
        ],
    )
    def test_auth_error_text_patterns(self, classifier, text_pattern):
        """Test authentication error text patterns are classified as permanent."""
        exception = pylast.WSError("LastFm", "999", f"Error: {text_pattern}")

        error_type, returned_code, description = classifier.classify_error(exception)

        assert error_type == "permanent"
        assert returned_code == "text"
        assert "Authentication/authorization error" in description

    # UNKNOWN ERRORS - Should return "unknown" with 5 retries
    def test_unknown_error_unrecognized_code(self, classifier):
        """Test unrecognized error codes are classified as unknown."""
        exception = pylast.WSError("LastFm", "999", "Unrecognized error message")

        error_type, returned_code, description = classifier.classify_error(exception)

        assert error_type == "unknown"
        assert returned_code == "N/A"
        assert description == str(exception)

    def test_non_wserror_exceptions(self, classifier):
        """Test non-WSError exceptions are classified as unknown."""
        exception = ValueError("Some other error")

        error_type, returned_code, description = classifier.classify_error(exception)

        assert error_type == "unknown"
        assert returned_code == "N/A"
        assert description == str(exception)
