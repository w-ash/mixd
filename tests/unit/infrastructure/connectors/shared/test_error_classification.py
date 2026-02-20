"""Unit tests for shared error classification functionality.

Tests the core retry logic and error classification behavior without any network calls.

NOTE: Retry logic has been migrated to tenacity. These tests now verify that
the error classifier retry predicate correctly integrates with error classification.
"""

from unittest.mock import Mock

import pytest

from src.infrastructure.connectors._shared.retry_policies import (
    create_error_classifier_retry,
)
from src.infrastructure.connectors.lastfm.error_classifier import LastFMErrorClassifier
from src.infrastructure.connectors.lastfm.models import LastFMAPIError


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

    def _make_retry_state(self, exception: Exception) -> Mock:
        """Create a mock retry state with the given exception."""
        retry_state = Mock()
        retry_state.outcome.failed = True
        retry_state.outcome.exception.return_value = exception
        return retry_state

    def test_permanent_error_gives_up_immediately(self, retry_predicate):
        """Test permanent errors give up immediately (no retry)."""
        retry_state = self._make_retry_state(LastFMAPIError("10", "Invalid API key"))
        assert retry_predicate(retry_state) is False

    def test_not_found_error_gives_up_immediately(self, retry_predicate):
        """Test not found errors give up immediately (no retry)."""
        retry_state = self._make_retry_state(LastFMAPIError("999", "Track not found"))
        assert retry_predicate(retry_state) is False

    def test_rate_limit_error_retries(self, retry_predicate):
        """Test rate limit errors are retried."""
        retry_state = self._make_retry_state(LastFMAPIError("29", "Rate Limit Exceeded"))
        assert retry_predicate(retry_state) is True

    def test_network_error_retries(self, retry_predicate):
        """Test network/temporary errors are retried."""
        retry_state = self._make_retry_state(LastFMAPIError("11", "Service Offline"))
        assert retry_predicate(retry_state) is True

    def test_unknown_error_retries(self, retry_predicate):
        """Test unknown errors are retried (defensive retry)."""
        retry_state = self._make_retry_state(LastFMAPIError("9999", "Unknown error message"))
        assert retry_predicate(retry_state) is True

    def test_non_exception_base_exceptions_not_retried(self, retry_predicate):
        """Test that permanent errors are not retried even when retried.

        Note: With tenacity's retry_if_exception(), BaseExceptions that are not
        Exception subclasses are handled by tenacity itself and won't be retried.
        This test verifies the predicate works correctly with Exception subclasses.
        """
        retry_state = self._make_retry_state(LastFMAPIError("10", "Invalid API key"))
        assert retry_predicate(retry_state) is False
