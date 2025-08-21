"""Tests for LastFM error classification and retry behavior."""

import pylast
import pytest

from src.infrastructure.connectors._shared.error_classification import (
    create_giveup_handler,
    should_giveup_on_error,
)
from src.infrastructure.connectors.lastfm.error_classifier import LastFMErrorClassifier


@pytest.mark.unit
class TestLastFMErrorClassification:
    """Test error classification for proper retry decisions."""

    def test_classify_rate_limit_errors(self):
        """Test that rate limit errors are properly classified."""
        classifier = LastFMErrorClassifier()

        # Test explicit error code 29
        error_29 = pylast.WSError("LastFm", "29", "Rate Limit Exceeded")
        error_type, code, desc = classifier.classify_error(error_29)
        assert error_type == "rate_limit"
        assert code == "29"
        assert "Rate Limit Exceded" in desc

        # Test rate limit in message
        rate_limit_msg = pylast.WSError("LastFm", "999", "rate limit exceeded")
        error_type, code, desc = classifier.classify_error(rate_limit_msg)
        assert error_type == "rate_limit"
        assert code == "29"
        assert "Rate Limit Exceded" in desc

    def test_classify_permanent_errors(self):
        """Test that permanent errors are properly classified."""
        permanent_cases = [
            ("2", "Invalid service"),
            ("3", "Invalid Method"),
            ("4", "Authentication Failed"),
            ("5", "Invalid format"),
            ("6", "Invalid parameters"),
            ("7", "Invalid resource specified"),
            ("10", "Invalid API key"),
            ("12", "Subscribers Only"),
            ("13", "Invalid method signature"),
            ("14", "Unauthorized Token"),
            ("15", "This item is not available for streaming"),
            ("17", "Login: User requires to be logged in"),
            ("18", "Trial Expired"),
            ("21", "Not Enough Members"),
            ("22", "Not Enough Fans"),
            ("23", "Not Enough Neighbours"),
            ("24", "No Peak Radio"),
            ("25", "Radio Not Found"),
            ("26", "API Key Suspended"),
            ("27", "Deprecated"),
        ]

        for error_code, expected_desc in permanent_cases:
            error = pylast.WSError("LastFm", error_code, expected_desc)
            error_type, code, desc = LastFMErrorClassifier().classify_error(error)
            assert error_type == "permanent", f"Error {error_code} should be permanent"
            assert code == error_code
            assert expected_desc.lower() in desc.lower()

    def test_classify_temporary_errors(self):
        """Test that temporary errors are properly classified."""
        temporary_cases = [
            ("8", "Operation failed"),
            ("9", "Invalid session key"),
            ("11", "Service Offline"),
            ("16", "Service temporarily unavailable"),
            ("20", "Not Enough Content"),
        ]

        for error_code, expected_desc in temporary_cases:
            error = pylast.WSError("LastFm", error_code, expected_desc)
            error_type, code, desc = LastFMErrorClassifier().classify_error(error)
            assert error_type == "temporary", f"Error {error_code} should be temporary"
            assert code == error_code
            # Check that the key part of the description is present
            key_words = expected_desc.lower().split()[:2]  # First two words
            assert all(word in desc.lower() for word in key_words)

    def test_classify_not_found_errors(self):
        """Test that not found errors are properly classified."""
        not_found_cases = [
            "track not found",
            "does not exist",
            "no such track",
            "artist not found",
        ]

        for message in not_found_cases:
            error = pylast.WSError("LastFm", "999", message)
            error_type, _code, desc = LastFMErrorClassifier().classify_error(error)
            assert error_type == "not_found", f"'{message}' should be not_found"
            assert desc == "Resource not found"

    def test_classify_unknown_errors(self):
        """Test that unknown errors are classified as such."""
        unknown_error = pylast.WSError("LastFm", "999", "Some unknown error")
        error_type, code, _desc = LastFMErrorClassifier().classify_error(unknown_error)
        assert error_type == "unknown"
        assert code == "N/A"

    def test_classify_non_ws_errors(self):
        """Test that non-WSError exceptions are handled."""
        network_error = Exception("Network connection failed")
        error_type, code, desc = LastFMErrorClassifier().classify_error(network_error)
        assert error_type == "unknown"
        assert code == "N/A"
        assert "Network connection failed" in desc

    def test_should_giveup_decisions(self):
        """Test giveup decisions based on error classification."""
        classifier = LastFMErrorClassifier()
        should_giveup = should_giveup_on_error(classifier)

        # Should give up on permanent errors
        permanent_error = pylast.WSError("LastFm", "10", "Invalid API key")
        assert should_giveup(permanent_error) is True

        # Should NOT give up on rate limits
        rate_limit_error = pylast.WSError("LastFm", "29", "Rate Limit Exceeded")
        assert should_giveup(rate_limit_error) is False

        # Should NOT give up on temporary errors
        temp_error = pylast.WSError("LastFm", "11", "Service Offline")
        assert should_giveup(temp_error) is False

        # Should NOT give up on not found (context dependent)
        not_found_error = pylast.WSError("LastFm", "999", "track not found")
        assert should_giveup(not_found_error) is False

        # Should NOT give up on unknown (be safe)
        unknown_error = pylast.WSError("LastFm", "999", "Unknown issue")
        assert should_giveup(unknown_error) is False

    def test_enhanced_giveup_handler_functionality(self):
        """Test that enhanced giveup handler processes error details correctly."""
        classifier = LastFMErrorClassifier()
        giveup_handler = create_giveup_handler(classifier, "lastfm")

        # We can't easily test the structured logging in unit tests, but we can test
        # that the handler processes the error classification correctly
        permanent_error = pylast.WSError("LastFm", "10", "Invalid API key")
        details = {
            "exception": permanent_error,
            "tries": 5,
            "elapsed": 30.5,
        }

        # Test that it doesn't crash and processes the error classification
        try:
            giveup_handler(details)
            # If we get here without exception, the handler worked
            assert True
        except Exception as e:
            pytest.fail(f"Enhanced giveup handler failed: {e}")

        # Verify the error classification is working correctly
        error_type, error_code, error_desc = classifier.classify_error(permanent_error)
        assert error_type == "permanent"
        assert error_code == "10"
        assert "Invalid API key" in error_desc


class TestLastFMErrorRetryIntegration:
    """Integration tests for error handling in retry scenarios."""

    def test_rate_limit_should_retry_indefinitely(self):
        """Test that rate limit errors never give up."""
        classifier = LastFMErrorClassifier()
        should_giveup = should_giveup_on_error(classifier)

        rate_limit_error = pylast.WSError("LastFm", "29", "Rate Limit Exceeded")

        # Should never give up regardless of how many tries
        assert should_giveup(rate_limit_error) is False

        # Error should be classified for constant delay retry
        error_type, _, _ = classifier.classify_error(rate_limit_error)
        assert error_type == "rate_limit"

    def test_permanent_errors_give_up_immediately(self):
        """Test that permanent errors give up without retrying."""
        classifier = LastFMErrorClassifier()
        should_giveup = should_giveup_on_error(classifier)

        permanent_errors = [
            pylast.WSError("LastFm", "10", "Invalid API key"),
            pylast.WSError("LastFm", "4", "Authentication Failed"),
            pylast.WSError("LastFm", "26", "API Key Suspended"),
        ]

        for error in permanent_errors:
            # Should give up immediately
            assert should_giveup(error) is True

            # Should be classified as permanent
            error_type, _, _ = classifier.classify_error(error)
            assert error_type == "permanent"

    def test_field_extraction_error_handling(self):
        """Test error handling during field extraction."""
        classifier = LastFMErrorClassifier()
        should_giveup = should_giveup_on_error(classifier)

        # This would be tested in integration with the actual from_pylast_track method
        # For now, verify that the error classification works for field extraction scenarios

        # Field not available (should continue with other fields)
        field_not_found = pylast.WSError("LastFm", "999", "track not found")
        error_type, _, _ = classifier.classify_error(field_not_found)
        assert error_type == "not_found"
        assert should_giveup(field_not_found) is False  # Continue with other fields

        # Rate limit during field extraction (should retry)
        field_rate_limit = pylast.WSError("LastFm", "29", "Rate Limit Exceeded")
        error_type, _, _ = classifier.classify_error(field_rate_limit)
        assert error_type == "rate_limit"
        assert should_giveup(field_rate_limit) is False  # Should retry
