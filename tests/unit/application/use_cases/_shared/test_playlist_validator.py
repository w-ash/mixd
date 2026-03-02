"""Tests for playlist validation utility functions.

Tests error message classification and exception categorization used in
connector playlist operations for retry and recovery decisions.
"""

from src.application.use_cases._shared.playlist_validator import (
    classify_connector_api_error,
    classify_database_error,
    is_auth_error_message,
    is_rate_limit_error,
)


class TestIsAuthErrorMessage:
    """Test authentication error detection from error messages."""

    def test_detects_auth_keyword(self):
        """Should detect 'auth' in error message."""
        assert is_auth_error_message("Authentication failed") is True

    def test_detects_auth_keyword_case_insensitive(self):
        """Should detect 'auth' regardless of case."""
        assert is_auth_error_message("AUTHORIZATION required") is True

    def test_detects_token_keyword(self):
        """Should detect 'token' in error message."""
        assert is_auth_error_message("Invalid token provided") is True

    def test_detects_token_keyword_case_insensitive(self):
        """Should detect 'token' regardless of case."""
        assert is_auth_error_message("TOKEN expired") is True

    def test_returns_false_for_unrelated_message(self):
        """Should return False for messages without auth/token keywords."""
        assert is_auth_error_message("Connection refused") is False

    def test_returns_false_for_empty_string(self):
        """Should return False for empty error message."""
        assert is_auth_error_message("") is False


class TestIsRateLimitError:
    """Test rate limit error detection from error messages."""

    def test_detects_rate_keyword(self):
        """Should detect 'rate' in error message."""
        assert is_rate_limit_error("Rate limit exceeded") is True

    def test_detects_rate_keyword_case_insensitive(self):
        """Should detect 'rate' regardless of case."""
        assert is_rate_limit_error("RATE limited") is True

    def test_detects_429_status_code(self):
        """Should detect '429' in error message."""
        assert is_rate_limit_error("HTTP 429 Too Many Requests") is True

    def test_returns_false_for_unrelated_message(self):
        """Should return False for messages without rate/429 keywords."""
        assert is_rate_limit_error("Server error 500") is False

    def test_returns_false_for_empty_string(self):
        """Should return False for empty error message."""
        assert is_rate_limit_error("") is False


class TestClassifyConnectorApiError:
    """Test connector API error classification via pattern matching."""

    def test_timeout_error_is_retryable(self):
        """TimeoutError should be classified as retryable."""
        result = classify_connector_api_error(TimeoutError("Request timed out"))

        assert result["error_type"] == "TimeoutError"
        assert result["is_retryable"] is True
        assert result["is_auth_error"] is False
        assert result["is_rate_limit"] is False

    def test_connection_error_is_retryable(self):
        """ConnectionError should be classified as retryable."""
        result = classify_connector_api_error(ConnectionError("Connection refused"))

        assert result["error_type"] == "ConnectionError"
        assert result["is_retryable"] is True

    def test_value_error_is_not_retryable(self):
        """ValueError should not be classified as retryable."""
        result = classify_connector_api_error(ValueError("Invalid input"))

        assert result["error_type"] == "ValueError"
        assert result["is_retryable"] is False

    def test_runtime_error_is_not_retryable(self):
        """RuntimeError should not be classified as retryable."""
        result = classify_connector_api_error(RuntimeError("Something broke"))

        assert result["error_type"] == "RuntimeError"
        assert result["is_retryable"] is False

    def test_auth_message_sets_auth_flag(self):
        """Error message containing 'auth' should set is_auth_error."""
        result = classify_connector_api_error(ValueError("Authentication required"))

        assert result["is_auth_error"] is True
        assert result["is_retryable"] is False

    def test_rate_limit_message_sets_rate_limit_flag(self):
        """Error message containing '429' should set is_rate_limit."""
        result = classify_connector_api_error(TimeoutError("HTTP 429 rate limited"))

        assert result["is_rate_limit"] is True
        assert result["is_retryable"] is True

    def test_combined_auth_and_retryable(self):
        """A retryable error with auth message should set both flags."""
        result = classify_connector_api_error(ConnectionError("token refresh failed"))

        assert result["is_retryable"] is True
        assert result["is_auth_error"] is True


class TestClassifyDatabaseError:
    """Test database error classification for retry and recovery."""

    def test_constraint_violation_detected(self):
        """Should detect constraint violation from error message."""
        result = classify_database_error(
            Exception("UNIQUE constraint failed: tracks.isrc")
        )

        assert result["error_type"] == "Exception"
        assert result["is_constraint_violation"] is True
        assert result["is_connection_error"] is False

    def test_unique_violation_detected(self):
        """Should detect 'unique' keyword as constraint violation."""
        result = classify_database_error(ValueError("unique key violated"))

        assert result["error_type"] == "ValueError"
        assert result["is_constraint_violation"] is True

    def test_connection_error_detected(self):
        """Should detect connection errors from error message."""
        result = classify_database_error(Exception("database connection lost"))

        assert result["is_connection_error"] is True
        assert result["is_constraint_violation"] is False

    def test_timeout_in_message_detected_as_connection_error(self):
        """Should detect 'timeout' as a connection error."""
        result = classify_database_error(Exception("database timeout after 30s"))

        assert result["is_connection_error"] is True

    def test_generic_error_no_flags(self):
        """Generic error without keywords should have all flags False."""
        result = classify_database_error(RuntimeError("something went wrong"))

        assert result["error_type"] == "RuntimeError"
        assert result["is_constraint_violation"] is False
        assert result["is_connection_error"] is False

    def test_preserves_exception_type_name(self):
        """Should preserve the original exception class name."""
        result = classify_database_error(TypeError("bad type"))
        assert result["error_type"] == "TypeError"
