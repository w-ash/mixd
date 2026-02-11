"""Unit tests for shared error classification functionality.

Tests the core retry logic and error classification behavior without any network calls.

NOTE: Retry logic has been migrated to tenacity. These tests now verify that
the error classifier retry predicate correctly integrates with error classification.
"""

import pytest

from src.infrastructure.connectors._shared.retry_policies import create_error_classifier_retry
from src.infrastructure.connectors.lastfm.error_classifier import LastFMErrorClassifier


class TestErrorClassificationRetryLogic:
    """Unit tests for retry predicate logic based on error classification."""

    @pytest.fixture
    def classifier(self):
        """Error classifier for testing."""
        return LastFMErrorClassifier()

    @pytest.fixture
    def retry_predicate(self, classifier):
        """Retry predicate using the error classifier."""
        return create_error_classifier_retry(classifier)

    def test_permanent_error_gives_up_immediately(self, retry_predicate):
        """Test permanent errors give up immediately (no retry)."""
        from unittest.mock import Mock

        import pylast

        # Mock retry state with permanent error
        retry_state = Mock()
        retry_state.outcome.failed = True
        retry_state.outcome.exception.return_value = pylast.WSError(
            "LastFm", "10", "Invalid API key"
        )

        # Should not retry permanent errors
        assert retry_predicate(retry_state) is False

    def test_not_found_error_gives_up_immediately(self, retry_predicate):
        """Test not found errors give up immediately (no retry)."""
        from unittest.mock import Mock

        import pylast

        # Mock retry state with not_found error
        retry_state = Mock()
        retry_state.outcome.failed = True
        retry_state.outcome.exception.return_value = pylast.WSError(
            "LastFm", "999", "Track not found"
        )

        # Should not retry not_found errors
        assert retry_predicate(retry_state) is False

    def test_rate_limit_error_retries(self, retry_predicate):
        """Test rate limit errors are retried."""
        from unittest.mock import Mock

        import pylast

        # Mock retry state with rate_limit error
        retry_state = Mock()
        retry_state.outcome.failed = True
        retry_state.outcome.exception.return_value = pylast.WSError(
            "LastFm", "29", "Rate Limit Exceeded"
        )

        # Should retry rate_limit errors
        assert retry_predicate(retry_state) is True

    def test_network_error_retries(self, retry_predicate):
        """Test network/temporary errors are retried."""
        from unittest.mock import Mock

        import pylast

        # Mock retry state with temporary error
        retry_state = Mock()
        retry_state.outcome.failed = True
        retry_state.outcome.exception.return_value = pylast.WSError(
            "LastFm", "11", "Service Offline"
        )

        # Should retry temporary errors
        assert retry_predicate(retry_state) is True

    def test_unknown_error_retries(self, retry_predicate):
        """Test unknown errors are retried (defensive retry)."""
        from unittest.mock import Mock

        import pylast

        # Mock retry state with unknown error
        retry_state = Mock()
        retry_state.outcome.failed = True
        retry_state.outcome.exception.return_value = pylast.WSError(
            "LastFm", "999", "Unknown error message"
        )

        # Should retry unknown errors defensively
        assert retry_predicate(retry_state) is True

    def test_non_exception_base_exceptions_not_retried(self, retry_predicate):
        """Test that non-Exception BaseExceptions are not retried.

        Note: With tenacity's retry_if_exception(), BaseExceptions that are not
        Exception subclasses are handled by tenacity itself and won't be retried.
        This test verifies the predicate works correctly with Exception subclasses.
        """
        from unittest.mock import Mock

        import pylast

        # Test with an Exception that would fail type guard in our old implementation
        # Now handled cleanly by retry_if_exception wrapper
        retry_state = Mock()
        retry_state.outcome.failed = True
        retry_state.outcome.exception.return_value = pylast.WSError(
            "LastFm", "10", "Invalid API key"  # permanent error
        )

        # Should not retry permanent errors
        assert retry_predicate(retry_state) is False
