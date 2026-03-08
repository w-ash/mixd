"""Integration tests for LastFM retry behavior with real tenacity policy.

Tests verify the interaction between the error classifier, retry predicate,
and the tenacity retry policy in the LastFMAPIClient. Classification logic
itself is tested in tests/unit/infrastructure/connectors/lastfm/test_error_classifier.py.
"""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.infrastructure.connectors.lastfm.client import LastFMAPIClient
from src.infrastructure.connectors.lastfm.models import LastFMAPIError

_MINIMAL_TRACK_DATA = {
    "track": {"name": "Test Track", "artist": {"name": "Test Artist"}}
}


@pytest.mark.slow
class TestLastFMRetryBehavior:
    """Tests for LastFM client retry policy integration."""

    @pytest.fixture
    def lastfm_client(self):
        """LastFM client with mocked settings."""
        with patch(
            "src.infrastructure.connectors.lastfm.client.settings"
        ) as mock_settings:
            mock_settings.credentials.lastfm_key = "test_key"
            mock_settings.credentials.lastfm_secret.get_secret_value.return_value = (
                "test_secret"
            )
            mock_settings.credentials.lastfm_username = "test_user"
            mock_settings.api.lastfm.rate_limit = 10.0
            mock_settings.api.lastfm.concurrency = 50
            mock_settings.api.lastfm.request_timeout = 10.0
            mock_settings.api.lastfm.retry_count = 8
            mock_settings.api.lastfm.retry_base_delay = 1.0
            mock_settings.api.lastfm.retry_max_delay = 60.0
            yield LastFMAPIClient()

    @pytest.fixture
    def fast_retry_client(self, lastfm_client):
        """Client with instant retries — no exponential backoff waits."""
        from tenacity import wait_none

        lastfm_client._retry_policy.wait = wait_none()
        return lastfm_client

    async def test_non_lastfm_exception_propagates(self, lastfm_client):
        """Non-LastFMAPIError exceptions propagate immediately without retries.

        ValueError from _api_request is a programming error. The retry policy
        only retries LastFMAPIError/httpx exceptions; others propagate to caller.
        """
        mock_api = AsyncMock(
            side_effect=ValueError("Programming error - not an API error")
        )

        with patch.object(LastFMAPIClient, "_api_request", mock_api):
            with pytest.raises(ValueError, match="Programming error"):
                await lastfm_client.get_track_info_comprehensive(
                    "Test Artist", "Test Track"
                )

        assert mock_api.call_count == 1

    async def test_error_classifier_retry_predicate_integration(self, lastfm_client):
        """Error classifier and retry predicate are correctly wired together."""
        from src.infrastructure.connectors._shared.retry_policies import (
            create_error_classifier_retry,
        )
        from src.infrastructure.connectors.lastfm.error_classifier import (
            LastFMErrorClassifier,
        )

        classifier = LastFMErrorClassifier()
        retry_predicate = create_error_classifier_retry(classifier)

        test_cases = [
            (LastFMAPIError("10", "Invalid API key"), "permanent", False),
            (LastFMAPIError("11", "Service Offline"), "temporary", True),
            (LastFMAPIError("29", "Rate Limit Exceeded"), "rate_limit", True),
            (LastFMAPIError("999", "Track not found"), "not_found", False),
            (LastFMAPIError("9999", "Unknown error code"), "unknown", True),
        ]

        for exception, expected_type, should_retry in test_cases:
            error_type, _error_code, _error_description = classifier.classify_error(
                exception
            )
            assert error_type == expected_type

            retry_state = Mock()
            retry_state.outcome.failed = True
            retry_state.outcome.exception.return_value = exception

            predicate_should_retry = retry_predicate(retry_state)
            assert predicate_should_retry == should_retry, (
                f"Retry predicate mismatch for {error_type}: "
                f"got {predicate_should_retry}, expected {should_retry}"
            )

    async def test_maximum_retry_exhaustion(self, fast_retry_client):
        """Temporary errors exhaust all retries then return None."""
        mock_api = AsyncMock(
            side_effect=LastFMAPIError("11", "Service Offline - Always fails")
        )

        with patch.object(LastFMAPIClient, "_api_request", mock_api):
            result = await fast_retry_client.get_track_info_comprehensive(
                "Test Artist", "Test Track"
            )

        assert result is None
        assert mock_api.call_count >= 3
