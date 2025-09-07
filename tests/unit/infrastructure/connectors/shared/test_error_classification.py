"""Unit tests for shared error classification functionality.

Tests the core retry logic and error classification behavior without any network calls.
"""


import pytest

from src.infrastructure.connectors._shared.error_classification import (
    should_giveup_on_error,
)
from src.infrastructure.connectors.lastfm.error_classifier import LastFMErrorClassifier


class TestErrorClassificationRetryLogic:
    """Unit tests for retry count logic based on error classification."""

    @pytest.fixture
    def classifier(self):
        """Error classifier for testing.""" 
        return LastFMErrorClassifier()

    def test_permanent_error_gives_up_immediately(self, classifier):
        """Test permanent errors give up immediately regardless of try count."""
        giveup_func = should_giveup_on_error(classifier, "lastfm")
        
        # Mock a permanent error
        import pylast
        exception = pylast.WSError("LastFm", "10", "Invalid API key")
        
        assert giveup_func(exception) is True  # Should give up immediately

    def test_not_found_error_gives_up_immediately(self, classifier):
        """Test not found errors give up immediately regardless of try count."""
        giveup_func = should_giveup_on_error(classifier, "lastfm")
        
        import pylast
        exception = pylast.WSError("LastFm", "999", "Track not found")
        
        assert giveup_func(exception) is True  # Should give up immediately

    def test_rate_limit_error_does_not_give_up(self, classifier):
        """Test rate limit errors do not give up (handled by max_tries in decorator)."""
        giveup_func = should_giveup_on_error(classifier, "lastfm")
        
        import pylast
        exception = pylast.WSError("LastFm", "29", "Rate Limit Exceeded")
        
        # Rate limit errors should not give up (let max_tries handle it)
        assert giveup_func(exception) is False

    def test_network_error_does_not_give_up(self, classifier):
        """Test network/temporary errors do not give up (handled by max_tries in decorator)."""
        giveup_func = should_giveup_on_error(classifier, "lastfm")
        
        import pylast
        exception = pylast.WSError("LastFm", "11", "Service Offline")
        
        # Network errors should not give up (let max_tries handle it)
        assert giveup_func(exception) is False

    def test_unknown_error_does_not_give_up(self, classifier):
        """Test unknown errors do not give up (handled by max_tries in decorator)."""
        giveup_func = should_giveup_on_error(classifier, "lastfm")
        
        import pylast
        exception = pylast.WSError("LastFm", "999", "Unknown error message")
        
        # Unknown errors should not give up (let max_tries handle it)
        assert giveup_func(exception) is False

    def test_non_lastfm_service_retryable_errors(self, classifier):
        """Test non-LastFM services handle retryable errors correctly."""
        giveup_func = should_giveup_on_error(classifier, "spotify")
        
        import pylast
        exception = pylast.WSError("Spotify", "unknown", "Some error")
        
        # Retryable errors should not give up (let max_tries handle it)
        assert giveup_func(exception) is False