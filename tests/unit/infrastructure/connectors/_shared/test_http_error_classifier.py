"""Unit tests for HTTPErrorClassifier base class.

This test suite validates the shared HTTP status code and text pattern
classification logic that all HTTP-based connectors (Spotify, Apple Music)
should inherit from.

Tests are written in TDD style (RED-GREEN-REFACTOR) to drive implementation
of the HTTPErrorClassifier base class.
"""

from http import HTTPStatus

import pytest

from src.infrastructure.connectors._shared.error_classification import (
    HTTPErrorClassifier,
)


class TestHTTPErrorClassifierImplementation(HTTPErrorClassifier):
    """Concrete test implementation for testing abstract base class."""

    @property
    def service_name(self) -> str:
        """Return test service name."""
        return "test_service"

    def classify_error(self, exception: Exception) -> tuple[str, str, str]:
        """Simple implementation that delegates to HTTP methods."""
        error_str = str(exception).lower()

        # Try HTTP status classification first
        if hasattr(exception, "status_code"):
            if result := self.classify_http_status(
                exception.status_code, str(exception)
            ):
                return result

        # Fall back to text pattern classification
        if result := self.classify_text_patterns(error_str):
            return result

        # Default fallback
        return ("unknown", "N/A", str(exception))


class TestHTTPErrorClassifierHTTPStatusCodes:
    """Test HTTP status code classification logic."""

    @pytest.fixture
    def classifier(self):
        """Create test classifier instance."""
        return TestHTTPErrorClassifierImplementation()

    # CLIENT ERROR TESTS (4xx) - Should be PERMANENT

    @pytest.mark.parametrize(
        ("status_code", "expected_type", "expected_code", "expected_desc_contains"),
        [
            (HTTPStatus.BAD_REQUEST, "permanent", "400", "bad request"),
            (HTTPStatus.UNAUTHORIZED, "permanent", "401", "unauthorized"),
            (HTTPStatus.FORBIDDEN, "permanent", "403", "forbidden"),
            (HTTPStatus.CONFLICT, "permanent", "409", "client error"),
            (HTTPStatus.UNPROCESSABLE_ENTITY, "permanent", "422", "client error"),
            (HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "permanent", "415", "client error"),
            (418, "permanent", "418", "client error"),  # I'm a teapot
        ],
    )
    def test_4xx_client_errors_are_permanent(
        self,
        classifier,
        status_code,
        expected_type,
        expected_code,
        expected_desc_contains,
    ):
        """Test that 4xx HTTP status codes are classified as permanent errors."""
        result = classifier.classify_http_status(status_code, "Test error")

        assert result is not None
        error_type, error_code, error_description = result

        assert error_type == expected_type
        assert error_code == expected_code
        assert expected_desc_contains in error_description.lower()

    def test_404_not_found_is_specific_category(self, classifier):
        """Test that 404 has its own category (not_found) instead of permanent."""
        result = classifier.classify_http_status(HTTPStatus.NOT_FOUND, "Not found")

        assert result is not None
        error_type, error_code, error_description = result

        assert error_type == "not_found"
        assert error_code == "404"
        assert "not found" in error_description.lower()

    def test_429_rate_limit_is_specific_category(self, classifier):
        """Test that 429 has its own category (rate_limit) instead of permanent."""
        result = classifier.classify_http_status(
            HTTPStatus.TOO_MANY_REQUESTS, "Too many requests"
        )

        assert result is not None
        error_type, error_code, error_description = result

        assert error_type == "rate_limit"
        assert error_code == "429"
        assert "rate limit" in error_description.lower()

    # SERVER ERROR TESTS (5xx) - Should be TEMPORARY

    @pytest.mark.parametrize(
        ("status_code", "expected_type", "expected_code", "expected_desc_contains"),
        [
            (
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "temporary",
                "500",
                "internal server error",
            ),
            (HTTPStatus.BAD_GATEWAY, "temporary", "502", "bad gateway"),
            (HTTPStatus.SERVICE_UNAVAILABLE, "temporary", "503", "service unavailable"),
            (HTTPStatus.GATEWAY_TIMEOUT, "temporary", "504", "gateway timeout"),
            (507, "temporary", "507", "server error"),  # Insufficient Storage
            (508, "temporary", "508", "server error"),  # Loop Detected
            (511, "temporary", "511", "server error"),  # Network Auth Required
            (599, "temporary", "599", "server error"),  # Edge case high 5xx
        ],
    )
    def test_5xx_server_errors_are_temporary(
        self,
        classifier,
        status_code,
        expected_type,
        expected_code,
        expected_desc_contains,
    ):
        """Test that 5xx HTTP status codes are classified as temporary errors."""
        result = classifier.classify_http_status(status_code, "Server error")

        assert result is not None
        error_type, error_code, error_description = result

        assert error_type == expected_type
        assert error_code == expected_code
        assert expected_desc_contains in error_description.lower()

    # EDGE CASES

    def test_unknown_status_code_returns_none(self, classifier):
        """Test that unknown HTTP status codes (not 4xx or 5xx) return None."""
        # 2xx success codes shouldn't be classified as errors
        result = classifier.classify_http_status(200, "OK")
        assert result is None

        # 3xx redirects shouldn't be classified as errors
        result = classifier.classify_http_status(301, "Moved Permanently")
        assert result is None

        # Completely invalid status codes
        result = classifier.classify_http_status(999, "Invalid")
        assert result is None

        result = classifier.classify_http_status(100, "Continue")
        assert result is None

    def test_none_status_code_returns_none(self, classifier):
        """Test that None status code returns None (not an error)."""
        result = classifier.classify_http_status(None, "No status")  # type: ignore
        assert result is None


class TestHTTPErrorClassifierTextPatterns:
    """Test text pattern classification logic."""

    @pytest.fixture
    def classifier(self):
        """Create test classifier instance."""
        return TestHTTPErrorClassifierImplementation()

    # RATE LIMIT PATTERNS

    @pytest.mark.parametrize(
        "error_text",
        [
            "Rate limit exceeded",
            "Too many requests",
            "Quota has been exceeded",
            "Request throttled",
            "API rate limit hit",
            "RATE_LIMIT_EXCEEDED",
            "too many api calls",
            "quota limit reached",
            "throttle active",
        ],
    )
    def test_rate_limit_text_patterns(self, classifier, error_text):
        """Test that rate limit text patterns are detected."""
        result = classifier.classify_text_patterns(error_text)

        assert result is not None
        error_type, error_code, error_description = result

        assert error_type == "rate_limit"
        assert error_code == "text"
        assert "rate limit" in error_description.lower()

    # NETWORK ERROR PATTERNS

    @pytest.mark.parametrize(
        "error_text",
        [
            "Connection timeout",
            "Network error occurred",
            "DNS resolution failed",
            "SSL certificate error",
            "connection refused",
            "TIMEOUT after 30 seconds",
            "network unreachable",
            "dns lookup failed",
            "ssl handshake failed",
        ],
    )
    def test_network_error_text_patterns(self, classifier, error_text):
        """Test that network error patterns are classified as temporary."""
        result = classifier.classify_text_patterns(error_text)

        assert result is not None
        error_type, error_code, error_description = result

        assert error_type == "temporary"
        assert error_code == "network"
        assert (
            "network" in error_description.lower()
            or "connection" in error_description.lower()
        )

    # AUTHENTICATION ERROR PATTERNS

    @pytest.mark.parametrize(
        "error_text",
        [
            "Unauthorized access",
            "Forbidden resource",
            "Invalid access token",
            "Token has expired",
            "UNAUTHORIZED request",
            "forbidden operation",
            "invalid token provided",
            "expired token detected",
        ],
    )
    def test_auth_error_text_patterns(self, classifier, error_text):
        """Test that auth error patterns are classified as permanent."""
        result = classifier.classify_text_patterns(error_text)

        assert result is not None
        error_type, error_code, error_description = result

        assert error_type == "permanent"
        assert error_code == "auth"
        assert "auth" in error_description.lower()

    # NOT FOUND PATTERNS

    @pytest.mark.parametrize(
        "error_text",
        [
            "Resource not found",
            "Track does not exist",
            "No such playlist",
            "Invalid ID provided",
            "NOT_FOUND error",
            "does not exist in database",
            "no such resource",
            "invalid id: abc123",
        ],
    )
    def test_not_found_text_patterns(self, classifier, error_text):
        """Test that not found patterns are classified correctly."""
        result = classifier.classify_text_patterns(error_text)

        assert result is not None
        error_type, error_code, error_description = result

        assert error_type == "not_found"
        assert error_code == "text"
        assert "not found" in error_description.lower()

    # TEMPORARY SERVICE ISSUE PATTERNS

    @pytest.mark.parametrize(
        "error_text",
        [
            "Service temporarily unavailable",
            "Server error occurred",
            "Internal error, please try again",
            "Temporarily down for maintenance",
            "System temporarily unavailable",
            "SERVER_ERROR internal",
            "please try again later",
            "service is temporarily offline",
            "unavailable right now",
        ],
    )
    def test_temporary_service_text_patterns(self, classifier, error_text):
        """Test that temporary service issue patterns are detected."""
        result = classifier.classify_text_patterns(error_text)

        assert result is not None
        error_type, error_code, error_description = result

        assert error_type == "temporary"
        assert error_code == "text"
        assert (
            "temporary" in error_description.lower()
            or "service" in error_description.lower()
        )

    # NO MATCH CASES

    @pytest.mark.parametrize(
        "error_text",
        [
            "Some random error",
            "Unexpected exception occurred",
            "ValueError: invalid input",
            "KeyError: missing key",
            "No pattern matches this text",
        ],
    )
    def test_no_pattern_match_returns_none(self, classifier, error_text):
        """Test that text without matching patterns returns None."""
        result = classifier.classify_text_patterns(error_text)

        assert result is None


class TestHTTPErrorClassifierIntegration:
    """Test how HTTP status and text pattern methods work together."""

    @pytest.fixture
    def classifier(self):
        """Create test classifier instance."""
        return TestHTTPErrorClassifierImplementation()

    def test_http_status_takes_precedence_over_text_patterns(self, classifier):
        """Test that HTTP status classification takes precedence."""

        # Create exception with both HTTP status and rate limit text
        class TestException(Exception):
            def __init__(self, message):
                super().__init__(message)
                self.status_code = 500  # Server error

        exc = TestException("Rate limit exceeded")  # Text says rate limit

        result = classifier.classify_error(exc)
        error_type, error_code, _ = result

        # Should use HTTP status (temporary) not text pattern (rate_limit)
        assert error_type == "temporary"
        assert error_code == "500"

    def test_text_patterns_used_when_no_http_status(self, classifier):
        """Test that text patterns are used when HTTP status isn't available."""
        exc = Exception("Rate limit exceeded")

        result = classifier.classify_error(exc)
        error_type, error_code, _ = result

        # Should use text pattern classification
        assert error_type == "rate_limit"
        assert error_code == "text"

    def test_unknown_fallback_when_no_classification_matches(self, classifier):
        """Test fallback to unknown when neither HTTP nor text patterns match."""

        class TestException(Exception):
            def __init__(self, message):
                super().__init__(message)
                # No status_code attribute

        exc = TestException("Completely unrecognized error")

        result = classifier.classify_error(exc)
        error_type, error_code, _ = result

        assert error_type == "unknown"
        assert error_code == "N/A"
