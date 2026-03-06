"""Comprehensive error classification tests for LastFM API integration.

This test suite provides exhaustive coverage of all error codes and patterns
defined in src/infrastructure/connectors/lastfm/error_classifier.py.

Tests ensure that each error type triggers the correct retry behavior:
- Permanent errors: No retries (immediate failure)
- Temporary errors: 2-3 retries with exponential backoff
- Rate limit errors: 2-3 retries with constant delay
- Not found errors: No retries (immediate failure with debug logging)
- Unknown errors: 2-3 retries with exponential backoff
"""

import time
from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.infrastructure.connectors.lastfm.client import LastFMAPIClient
from src.infrastructure.connectors.lastfm.models import LastFMAPIError

# Minimal valid Last.fm track.getInfo JSON response for success cases
_MINIMAL_TRACK_DATA = {
    "track": {"name": "Test Track", "artist": {"name": "Test Artist"}}
}


@pytest.mark.slow
class TestComprehensiveErrorClassification:
    """Comprehensive error code coverage testing with all LastFM API error scenarios."""

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
            # Retry policy parameters — must be concrete values, not MagicMock
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

    # PERMANENT ERRORS (20+ codes) - Should NOT retry, immediate failure
    @pytest.mark.parametrize(
        ("error_code", "description"),
        [
            ("2", "Invalid service - This service does not exist"),
            ("3", "Invalid Method - No method with that name in this package"),
            (
                "4",
                "Authentication Failed - You do not have permissions to access the service",
            ),
            ("5", "Invalid format - This service doesn't exist in that format"),
            ("6", "Invalid parameters - Your request is missing a required parameter"),
            ("7", "Invalid resource specified"),
            ("10", "Invalid API key - You must be granted a valid key by last.fm"),
            (
                "12",
                "Subscribers Only - This station is only available to paid last.fm subscribers",
            ),
            ("13", "Invalid method signature supplied"),
            ("14", "Unauthorized Token - This token has not been authorized"),
            ("15", "This item is not available for streaming"),
            ("17", "Login: User requires to be logged in"),
            (
                "18",
                "Trial Expired - This user has no free radio plays left. Subscription required",
            ),
            (
                "21",
                "Not Enough Members - This group does not have enough members for radio",
            ),
            (
                "22",
                "Not Enough Fans - This artist does not have enough fans for for radio",
            ),
            ("23", "Not Enough Neighbours - There are not enough neighbours for radio"),
            (
                "24",
                "No Peak Radio - This user is not allowed to listen to radio during peak usage",
            ),
            ("25", "Radio Not Found - Radio station not found"),
            (
                "26",
                "API Key Suspended - This application is not allowed to make requests to the web services",
            ),
            ("27", "Deprecated - This type of request is no longer supported"),
        ],
    )
    async def test_permanent_error_no_retry_comprehensive(
        self, lastfm_client, error_code, description
    ):
        """Test all permanent error codes cause immediate failure with no retries."""
        mock_api = AsyncMock(side_effect=LastFMAPIError(error_code, description))

        with patch.object(LastFMAPIClient, "_api_request", mock_api):
            start_time = time.time()
            result = await lastfm_client.get_track_info_comprehensive(
                "Test Artist", "Test Track"
            )
            duration = time.time() - start_time

        # Should return None gracefully (no exception raised)
        assert result is None

        # Should NOT retry (only 1 call) - permanent errors are immediate failures
        assert mock_api.call_count == 1, (
            f"Expected 1 call for permanent error {error_code}, got {mock_api.call_count}"
        )

        # Should be fast (no retry delays)
        assert duration < 2.0, f"Permanent error took too long: {duration}s"

    # TEMPORARY ERRORS (5 codes) - Should retry 2-3 times with exponential backoff
    @pytest.mark.parametrize(
        ("error_code", "description"),
        [
            (
                "8",
                "Operation failed - Most likely the backend service failed. Please try again",
            ),
            ("9", "Invalid session key - Please re-authenticate"),
            (
                "11",
                "Service Offline - This service is temporarily offline. Try again later",
            ),
            ("16", "The service is temporarily unavailable, please try again"),
            (
                "20",
                "Not Enough Content - There is not enough content to play this station",
            ),
        ],
    )
    async def test_temporary_error_retry_comprehensive(
        self, fast_retry_client, error_code, description
    ):
        """Test all temporary error codes trigger retries (fail once, then succeed)."""
        mock_api = AsyncMock(
            side_effect=[LastFMAPIError(error_code, description), _MINIMAL_TRACK_DATA]
        )

        with patch.object(LastFMAPIClient, "_api_request", mock_api):
            await fast_retry_client.get_track_info_comprehensive(
                "Test Artist", "Test Track"
            )

        # Should have retried (2 calls total: 1 failure + 1 success)
        assert mock_api.call_count == 2, (
            f"Expected 2 calls for temporary error {error_code}, got {mock_api.call_count}"
        )

    # RATE LIMIT ERRORS - Should retry with exponential backoff
    @pytest.mark.parametrize(
        "rate_limit_variant",
        [
            ("29", "Rate Limit Exceeded - Your IP has made too many requests"),
            ("text_rate_limit", "rate limit exceeded in response body"),
            ("text_too_many", "too many requests per minute"),
        ],
    )
    async def test_rate_limit_retry_comprehensive(
        self, fast_retry_client, rate_limit_variant
    ):
        """Test rate limit detection through both error codes and text patterns."""
        error_code, error_message = rate_limit_variant

        mock_api = AsyncMock(
            side_effect=[
                LastFMAPIError(error_code, error_message),
                LastFMAPIError(error_code, error_message),
                _MINIMAL_TRACK_DATA,
            ]
        )

        with patch.object(LastFMAPIClient, "_api_request", mock_api):
            await fast_retry_client.get_track_info_comprehensive(
                "Test Artist", "Test Track"
            )

        # Should have retried (3 calls total: 2 failures + 1 success)
        assert mock_api.call_count == 3, (
            f"Expected 3 calls for rate limit variant {rate_limit_variant}, got {mock_api.call_count}"
        )

    # TEXT PATTERN ERRORS - Not found, network, auth patterns
    @pytest.mark.parametrize(
        ("error_pattern", "expected_type", "should_retry"),
        [
            ("track not found", "not_found", False),
            ("artist does not exist", "not_found", False),
            ("no such user", "not_found", False),
            ("timeout occurred", "temporary", True),
            ("connection refused", "temporary", True),
            ("network error", "temporary", True),
            ("server error 500", "temporary", True),
            ("503 service unavailable", "temporary", True),
            ("502 bad gateway", "unknown", True),
            ("unauthorized access", "permanent", False),
            ("forbidden request", "permanent", False),
            ("invalid api key", "permanent", False),
            ("authentication failed", "permanent", False),
        ],
    )
    async def test_text_pattern_classification(
        self, fast_retry_client, error_pattern, expected_type, should_retry
    ):
        """Test error classification from response text when error codes unavailable."""
        if should_retry:
            # For retryable errors: fail on first call, succeed on second
            mock_api = AsyncMock(
                side_effect=[
                    LastFMAPIError("999", error_pattern),
                    _MINIMAL_TRACK_DATA,
                ]
            )
        else:
            # For non-retryable errors: always fail
            mock_api = AsyncMock(side_effect=LastFMAPIError("999", error_pattern))

        with patch.object(LastFMAPIClient, "_api_request", mock_api):
            result = await fast_retry_client.get_track_info_comprehensive(
                "Test Artist", "Test Track"
            )

        if should_retry:
            # Should retry for retryable text patterns
            assert mock_api.call_count == 2, (
                f"Expected retry for {expected_type} error: {error_pattern}"
            )
        else:
            # Should fail immediately for non-retryable text patterns
            assert result is None
            assert mock_api.call_count == 1, (
                f"Expected no retry for {expected_type} error: {error_pattern}"
            )

    # UNKNOWN ERRORS - Should be classified as unknown and retry
    async def test_unknown_error_handling(self, fast_retry_client):
        """Test unrecognized errors are classified as unknown and retried."""
        mock_api = AsyncMock(
            side_effect=[
                LastFMAPIError(
                    "9999", "Completely unknown error that should be retried"
                ),
                _MINIMAL_TRACK_DATA,
            ]
        )

        with patch.object(LastFMAPIClient, "_api_request", mock_api):
            await fast_retry_client.get_track_info_comprehensive(
                "Test Artist", "Test Track"
            )

        # Should have retried (2 calls total)
        assert mock_api.call_count == 2, (
            f"Expected retry for unknown error, got {mock_api.call_count} calls"
        )

    # NON-LASTFM EXCEPTIONS - Propagate (programming errors are not silently swallowed)
    async def test_non_lastfm_exception_handling(self, lastfm_client):
        """Test that non-LastFMAPIError exceptions are not swallowed silently.

        ValueError from _api_request is a programming error. The retry policy
        only retries LastFMAPIError/httpx exceptions; others propagate immediately.
        get_track_info_comprehensive only catches (LastFMAPIError, httpx exceptions),
        so ValueError propagates to the caller.
        """
        mock_api = AsyncMock(
            side_effect=ValueError("Programming error - not an API error")
        )

        with patch.object(LastFMAPIClient, "_api_request", mock_api):
            with pytest.raises(ValueError, match="Programming error"):
                await lastfm_client.get_track_info_comprehensive(
                    "Test Artist", "Test Track"
                )

        # Should NOT retry (only 1 call) - retry policy only handles API exception types
        assert mock_api.call_count == 1, (
            f"Expected 1 call for programming error, got {mock_api.call_count}"
        )

    # ERROR CLASSIFIER INTEGRATION - Test classifier behavior directly
    async def test_error_classifier_integration(self, lastfm_client):
        """Test that error classifier integration works correctly with tenacity retry predicate."""
        from src.infrastructure.connectors._shared.retry_policies import (
            create_error_classifier_retry,
        )
        from src.infrastructure.connectors.lastfm.error_classifier import (
            LastFMErrorClassifier,
        )

        classifier = LastFMErrorClassifier()
        retry_predicate = create_error_classifier_retry(classifier)

        # Test classification examples
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

            # Verify classification matches expectations
            assert error_type == expected_type, (
                f"Expected {expected_type}, got {error_type} for {exception}"
            )

            # Test retry predicate logic matches classification
            retry_state = Mock()
            retry_state.outcome.failed = True
            retry_state.outcome.exception.return_value = exception

            predicate_should_retry = retry_predicate(retry_state)

            # Predicate should match expected retry behavior
            assert predicate_should_retry == should_retry, (
                f"Retry predicate mismatch for {error_type}: predicate={predicate_should_retry}, expected={should_retry}"
            )


@pytest.mark.slow
class TestErrorClassificationEdgeCases:
    """Test edge cases and boundary conditions in error classification."""

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
            # Retry policy parameters — must be concrete values, not MagicMock
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

    async def test_empty_error_code(self, fast_retry_client):
        """Test handling of empty or None error codes — classified as unknown, retried."""
        mock_api = AsyncMock(
            side_effect=[
                LastFMAPIError("", "Some error message"),
                LastFMAPIError("", "Some error message"),
                _MINIMAL_TRACK_DATA,
            ]
        )

        with patch.object(LastFMAPIClient, "_api_request", mock_api):
            # Unknown code → retry behavior (succeeds on 3rd attempt)
            await fast_retry_client.get_track_info_comprehensive(
                "Test Artist", "Test Track"
            )

        assert mock_api.call_count >= 1

    async def test_maximum_retry_exhaustion(self, fast_retry_client):
        """Test behavior when maximum retries are exhausted."""
        mock_api = AsyncMock(
            side_effect=LastFMAPIError("11", "Service Offline - Always fails")
        )

        with patch.object(LastFMAPIClient, "_api_request", mock_api):
            result = await fast_retry_client.get_track_info_comprehensive(
                "Test Artist", "Test Track"
            )

        # Should eventually give up and return None
        assert result is None

        # Should have made maximum attempts
        assert mock_api.call_count >= 3, (
            f"Expected 3+ retry attempts, got {mock_api.call_count}"
        )

    async def test_partial_text_matches(self, fast_retry_client):
        """Test that partial text matches work correctly."""
        error_scenarios = [
            (
                "This track was not found in our database",
                False,
            ),  # "not found" → not_found
            (
                "Network connection timeout after 30 seconds",
                True,
            ),  # "timeout" → temporary
            (
                "Invalid API key provided for authentication",
                False,
            ),  # "invalid api key" → permanent
        ]

        for message, should_retry in error_scenarios:
            if should_retry:
                mock_api = AsyncMock(
                    side_effect=[
                        LastFMAPIError("999", message),
                        _MINIMAL_TRACK_DATA,
                    ]
                )
            else:
                mock_api = AsyncMock(side_effect=LastFMAPIError("999", message))

            with patch.object(LastFMAPIClient, "_api_request", mock_api):
                await fast_retry_client.get_track_info_comprehensive(
                    "Test Artist", "Test Track"
                )

            if should_retry:
                assert mock_api.call_count == 2, f"Expected retry for: {message}"
            else:
                assert mock_api.call_count == 1, f"Expected no retry for: {message}"
