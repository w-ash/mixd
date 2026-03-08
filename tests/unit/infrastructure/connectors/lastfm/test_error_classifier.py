"""Unit tests for LastFMErrorClassifier.

Tests focus on the pure classification logic:
- LastFM service-specific error codes (permanent, temporary, rate limit)
- Text pattern fallback (inherited from HTTPErrorClassifier)
- Edge cases: unknown codes, empty codes, non-LastFM exceptions
"""

import pytest

from src.infrastructure.connectors.lastfm.error_classifier import (
    LastFMErrorClassifier,
)
from src.infrastructure.connectors.lastfm.models import LastFMAPIError


class TestLastFMErrorClassifier:
    """Unit tests for LastFMErrorClassifier classification logic."""

    @pytest.fixture
    def classifier(self):
        """Create a LastFMErrorClassifier instance."""
        return LastFMErrorClassifier()

    def test_service_name(self, classifier):
        """Test that service name is correctly reported."""
        assert classifier.service_name == "lastfm"

    # PERMANENT ERROR CODES (20 codes)

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
        """Test all 20 permanent error codes are classified correctly."""
        exception = LastFMAPIError(error_code, "some error")

        error_type, code, _desc = classifier.classify_error(exception)

        assert error_type == "permanent"
        assert code == error_code

    # TEMPORARY ERROR CODES (5 codes)

    @pytest.mark.parametrize(
        "error_code",
        ["8", "9", "11", "16", "20"],
    )
    def test_temporary_error_codes(self, classifier, error_code):
        """Test all 5 temporary error codes are classified correctly."""
        exception = LastFMAPIError(error_code, "some error")

        error_type, code, _desc = classifier.classify_error(exception)

        assert error_type == "temporary"
        assert code == error_code

    # RATE LIMIT ERROR CODE

    def test_rate_limit_error_code(self, classifier):
        """Test error code 29 is classified as rate_limit."""
        exception = LastFMAPIError("29", "Rate Limit Exceeded")

        error_type, code, _desc = classifier.classify_error(exception)

        assert error_type == "rate_limit"
        assert code == "29"

    # TEXT PATTERN FALLBACK (unknown codes fall through to base class)

    @pytest.mark.parametrize(
        ("message", "expected_type"),
        [
            ("track not found", "not_found"),
            ("artist does not exist", "not_found"),
            ("no such user", "not_found"),
            ("timeout occurred", "temporary"),
            ("connection refused", "temporary"),
            ("network error", "temporary"),
            ("unauthorized access", "permanent"),
            ("forbidden request", "permanent"),
            ("invalid api key", "permanent"),
            ("authentication failed", "permanent"),
            ("rate limit exceeded", "rate_limit"),
            ("too many requests", "rate_limit"),
        ],
    )
    def test_text_pattern_fallback(self, classifier, message, expected_type):
        """Unknown error code falls through to text pattern classification."""
        exception = LastFMAPIError("999", message)

        error_type, _code, _desc = classifier.classify_error(exception)

        assert error_type == expected_type

    # EDGE CASES

    def test_unknown_code_no_text_match(self, classifier):
        """Completely unknown error code with no text pattern match → unknown."""
        exception = LastFMAPIError("9999", "xyz unrecognizable error")

        error_type, code, _desc = classifier.classify_error(exception)

        assert error_type == "unknown"
        assert code == "N/A"

    def test_empty_error_code(self, classifier):
        """Empty error code falls through to text patterns or unknown."""
        exception = LastFMAPIError("", "some error message")

        error_type, _code, _desc = classifier.classify_error(exception)

        # Empty code isn't in any dict → falls through to text patterns
        assert error_type in (
            "unknown",
            "temporary",
            "permanent",
            "not_found",
            "rate_limit",
        )

    def test_non_lastfm_error_bypasses_service_classification(self, classifier):
        """Non-LastFMAPIError exceptions skip service-specific logic."""
        exception = ValueError("not a lastfm error")

        error_type, code, _desc = classifier.classify_error(exception)

        assert error_type == "unknown"
        assert code == "N/A"
