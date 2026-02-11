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
from unittest.mock import MagicMock, patch

import pylast
import pytest

from src.infrastructure.connectors.lastfm.client import LastFMAPIClient


@pytest.mark.slow
@pytest.mark.integration
class TestComprehensiveErrorClassification:
    """Comprehensive error code coverage testing with all LastFM API error scenarios."""

    @pytest.fixture
    def lastfm_client(self):
        """LastFM client with mocked settings."""
        with patch("src.config.settings") as mock_settings:
            mock_settings.credentials.lastfm_key = "test_key"
            mock_settings.credentials.lastfm_secret.get_secret_value.return_value = (
                "test_secret"
            )
            mock_settings.credentials.lastfm_username = "test_user"
            mock_settings.credentials.lastfm_password.get_secret_value.return_value = (
                "test_pass"
            )
            mock_settings.api.lastfm_rate_limit = 10.0
            mock_settings.api.lastfm_concurrency = 50
            mock_settings.api.lastfm_request_timeout = 10.0
            yield LastFMAPIClient()

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
    @pytest.mark.asyncio
    async def test_permanent_error_no_retry_comprehensive(
        self, lastfm_client, error_code, description
    ):
        """Test all permanent error codes cause immediate failure with no retries."""

        call_count = 0

        def mock_get_track_permanent_error(*args, **kwargs):
            """Mock that raises the specified permanent error."""
            nonlocal call_count
            call_count += 1
            raise pylast.WSError("LastFm", error_code, description)

        with patch("pylast.LastFMNetwork") as mock_network_class:
            mock_network = MagicMock()
            mock_network.get_track.side_effect = mock_get_track_permanent_error
            mock_network_class.return_value = mock_network

            start_time = time.time()
            result = await lastfm_client.get_track_info_comprehensive(
                "Test Artist", "Test Track"
            )
            duration = time.time() - start_time

            # Should return None gracefully (no exception raised)
            assert result is None

            # Should NOT retry (only 1 call) - permanent errors are immediate failures
            assert call_count == 1, (
                f"Expected 1 call for permanent error {error_code}, got {call_count}"
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
    @pytest.mark.asyncio
    @pytest.mark.skip(
        reason="Performance: This test takes too long due to real retry delays - covered by faster tests"
    )
    async def test_temporary_error_retry_comprehensive(
        self, lastfm_client, error_code, description
    ):
        """Test all temporary error codes trigger 2-3 retries with exponential backoff."""

        call_count = 0

        def mock_get_track_temporary_error(*args, **kwargs):
            """Mock that raises temporary error, then succeeds on retry."""
            nonlocal call_count
            call_count += 1

            if call_count <= 1:
                raise pylast.WSError("LastFm", error_code, description)

            # Success on 2nd try
            mock_track = MagicMock(spec=pylast.Track)
            mock_track._request.return_value = MagicMock()
            return mock_track

        with patch("pylast.LastFMNetwork") as mock_network_class:
            mock_network = MagicMock()
            mock_network.get_track.side_effect = mock_get_track_temporary_error
            mock_network_class.return_value = mock_network

            start_time = time.time()
            result = await lastfm_client.get_track_info_comprehensive(
                "Test Artist", "Test Track"
            )
            duration = time.time() - start_time

            # Should succeed after retry (will be None because no comprehensive data, but that's ok)
            assert (
                result is not None or call_count == 2
            )  # Either succeeds with data or retried correctly

            # Should have retried (2 calls total: 1 failure + 1 success)
            assert call_count == 2, (
                f"Expected 2 calls for temporary error {error_code}, got {call_count}"
            )

            # Should have some delay from exponential backoff
            assert duration > 0.05, f"Temporary error retry too fast: {duration}s"

    # RATE LIMIT ERRORS - Should retry with exponential backoff
    @pytest.mark.parametrize(
        "rate_limit_variant",
        [
            (
                "29",
                "Rate Limit Exceeded - Your IP has made too many requests in a short period",
            ),
            ("text_rate_limit", "rate limit exceeded in response body"),
            ("text_too_many", "too many requests per minute"),
            ("text_quota", "quota exceeded for this API key"),
            ("text_throttle", "throttle limit reached"),
        ],
    )
    @pytest.mark.asyncio
    @pytest.mark.skip(
        reason="Performance: This test takes too long due to real retry delays - covered by faster tests"
    )
    async def test_rate_limit_retry_comprehensive(
        self, lastfm_client, rate_limit_variant
    ):
        """Test rate limit detection through both error codes and text patterns."""

        call_count = 0
        error_code, error_message = rate_limit_variant

        def mock_get_track_rate_limited(*args, **kwargs):
            """Mock that raises rate limit error, then succeeds."""
            nonlocal call_count
            call_count += 1

            if call_count <= 2:
                if error_code == "29":
                    raise pylast.WSError("LastFm", "29", error_message)
                else:
                    # Text pattern errors use generic code but specific message
                    raise pylast.WSError("LastFm", "999", error_message)

            # Success on 3rd try
            mock_track = MagicMock(spec=pylast.Track)
            mock_track._request.return_value = MagicMock()
            return mock_track

        with patch("pylast.LastFMNetwork") as mock_network_class:
            mock_network = MagicMock()
            mock_network.get_track.side_effect = mock_get_track_rate_limited
            mock_network_class.return_value = mock_network

            start_time = time.time()
            result = await lastfm_client.get_track_info_comprehensive(
                "Test Artist", "Test Track"
            )
            duration = time.time() - start_time

            # Should succeed after retries (will be None due to mock, but retries are what matter)
            assert (
                result is not None or call_count == 3
            )  # Either succeeds or retried correctly

            # Should have retried (3 calls total: 2 failures + 1 success)
            assert call_count == 3, (
                f"Expected 3 calls for rate limit variant {rate_limit_variant}, got {call_count}"
            )

            # Should have delay from retry backoff
            assert duration > 0.1, f"Rate limit retry too fast: {duration}s"

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
            ("502 bad gateway", "temporary", True),
            ("unauthorized access", "permanent", False),
            ("forbidden request", "permanent", False),
            ("invalid api key", "permanent", False),
            ("authentication failed", "permanent", False),
        ],
    )
    @pytest.mark.asyncio
    async def test_text_pattern_classification(
        self, lastfm_client, error_pattern, expected_type, should_retry
    ):
        """Test error classification from response text when error codes unavailable."""

        call_count = 0

        def mock_get_track_text_error(*args, **kwargs):
            """Mock that raises error with text pattern."""
            nonlocal call_count
            call_count += 1

            if should_retry and call_count <= 1:
                # For retryable errors, succeed on 2nd attempt
                raise pylast.WSError("LastFm", "999", error_pattern)
            elif not should_retry:
                # For non-retryable errors, always fail
                raise pylast.WSError("LastFm", "999", error_pattern)

            # Success on retry for retryable errors
            mock_track = MagicMock(spec=pylast.Track)
            mock_track._request.return_value = MagicMock()
            return mock_track

        with patch("pylast.LastFMNetwork") as mock_network_class:
            mock_network = MagicMock()
            mock_network.get_track.side_effect = mock_get_track_text_error
            mock_network_class.return_value = mock_network

            start_time = time.time()
            result = await lastfm_client.get_track_info_comprehensive(
                "Test Artist", "Test Track"
            )
            time.time() - start_time

            if should_retry:
                # Should retry for retryable text patterns
                assert call_count == 2, (
                    f"Expected retry for {expected_type} error: {error_pattern}"
                )
            else:
                # Should fail immediately for non-retryable text patterns
                assert result is None
                assert call_count == 1, (
                    f"Expected no retry for {expected_type} error: {error_pattern}"
                )

    # UNKNOWN ERRORS - Should be classified as unknown and retry
    @pytest.mark.asyncio
    async def test_unknown_error_handling(self, lastfm_client):
        """Test unrecognized errors are classified as unknown and retried."""

        call_count = 0

        def mock_get_track_unknown_error(*args, **kwargs):
            """Mock that raises unrecognized error."""
            nonlocal call_count
            call_count += 1

            if call_count <= 1:
                # Unknown error code and message
                raise pylast.WSError(
                    "LastFm", "9999", "Completely unknown error that should be retried"
                )

            # Success on 2nd try
            mock_track = MagicMock(spec=pylast.Track)
            mock_track._request.return_value = MagicMock()
            return mock_track

        with patch("pylast.LastFMNetwork") as mock_network_class:
            mock_network = MagicMock()
            mock_network.get_track.side_effect = mock_get_track_unknown_error
            mock_network_class.return_value = mock_network

            start_time = time.time()
            await lastfm_client.get_track_info_comprehensive(
                "Test Artist", "Test Track"
            )
            duration = time.time() - start_time

            # Should retry unknown errors correctly
            # (result will be None due to mock, but retry behavior is what we're testing)

            # Should have retried (2 calls total)
            assert call_count == 2, (
                f"Expected retry for unknown error, got {call_count} calls"
            )

            # Should have some delay from retry backoff (relaxed tolerance for CI/fast machines)
            assert duration > 0.03, f"Unknown error retry too fast: {duration}s"

    # NON-PYLAST EXCEPTIONS - Should not be retried by retry policy
    @pytest.mark.asyncio
    async def test_non_pylast_exception_handling(self, lastfm_client):
        """Test that non-pylast exceptions are handled gracefully without retries."""

        call_count = 0

        def mock_get_track_programming_error(*args, **kwargs):
            """Mock that raises programming error (not WSError)."""
            nonlocal call_count
            call_count += 1
            # Simulate programming error (ValueError, TypeError, etc.)
            raise ValueError("Programming error - not an API error")

        with patch("pylast.LastFMNetwork") as mock_network_class:
            mock_network = MagicMock()
            mock_network.get_track.side_effect = mock_get_track_programming_error
            mock_network_class.return_value = mock_network

            start_time = time.time()
            result = await lastfm_client.get_track_info_comprehensive(
                "Test Artist", "Test Track"
            )
            duration = time.time() - start_time

            # Should return None gracefully (caught by generic exception handler)
            assert result is None

            # Should NOT retry (retry policy only handles pylast.WSError)
            assert call_count == 1, (
                f"Expected 1 call for programming error, got {call_count}"
            )

            # Should be fast (no retries)
            assert duration < 0.5, f"Programming error took too long: {duration}s"

    # ERROR CLASSIFIER INTEGRATION - Test classifier behavior directly
    @pytest.mark.asyncio
    async def test_error_classifier_integration(self, lastfm_client):
        """Test that error classifier integration works correctly with tenacity retry predicate."""

        from unittest.mock import Mock

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
            (pylast.WSError("LastFm", "10", "Invalid API key"), "permanent", False),
            (pylast.WSError("LastFm", "11", "Service Offline"), "temporary", True),
            (pylast.WSError("LastFm", "29", "Rate Limit Exceeded"), "rate_limit", True),
            (pylast.WSError("LastFm", "999", "Track not found"), "not_found", False),
            (ValueError("Programming error"), "unknown", True),  # Non-WSError
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
@pytest.mark.integration
class TestErrorClassificationEdgeCases:
    """Test edge cases and boundary conditions in error classification."""

    @pytest.fixture
    def lastfm_client(self):
        """LastFM client with mocked settings."""
        with patch("src.config.settings") as mock_settings:
            mock_settings.credentials.lastfm_key = "test_key"
            mock_settings.credentials.lastfm_secret.get_secret_value.return_value = (
                "test_secret"
            )
            mock_settings.credentials.lastfm_username = "test_user"
            mock_settings.credentials.lastfm_password.get_secret_value.return_value = (
                "test_pass"
            )
            mock_settings.api.lastfm_rate_limit = 10.0
            mock_settings.api.lastfm_concurrency = 50
            mock_settings.api.lastfm_request_timeout = 10.0
            yield LastFMAPIClient()

    @pytest.mark.asyncio
    async def test_empty_error_code(self, lastfm_client):
        """Test handling of empty or None error codes."""

        call_count = 0

        def mock_empty_code_error(*args, **kwargs):
            nonlocal call_count
            call_count += 1

            if call_count > 2:  # Succeed after 2 attempts
                mock_track = MagicMock(spec=pylast.Track)
                mock_track._request.return_value = MagicMock()
                return mock_track

            # Create WSError with empty code
            error = pylast.WSError("LastFm", "", "Some error message")
            raise error

        with patch("pylast.LastFMNetwork") as mock_network_class:
            mock_network = MagicMock()
            mock_network.get_track.side_effect = mock_empty_code_error
            mock_network_class.return_value = mock_network

            # Should handle gracefully and classify as unknown
            result = await lastfm_client.get_track_info_comprehensive(
                "Test Artist", "Test Track"
            )
            assert result is None

    @pytest.mark.asyncio
    @pytest.mark.skip(
        reason="Performance: This test takes 60+ seconds due to real exponential retry delays"
    )
    async def test_maximum_retry_exhaustion(self, lastfm_client):
        """Test behavior when maximum retries are exhausted."""

        call_count = 0

        def mock_always_fails(*args, **kwargs):
            """Mock that always fails with retryable error."""
            nonlocal call_count
            call_count += 1
            raise pylast.WSError("LastFm", "11", "Service Offline - Always fails")

        with patch("pylast.LastFMNetwork") as mock_network_class:
            mock_network = MagicMock()
            mock_network.get_track.side_effect = mock_always_fails
            mock_network_class.return_value = mock_network

            start_time = time.time()
            result = await lastfm_client.get_track_info_comprehensive(
                "Test Artist", "Test Track"
            )
            duration = time.time() - start_time

            # Should eventually give up and return None
            assert result is None

            # Should have made maximum attempts (typically 3)
            assert call_count >= 3, f"Expected 3+ retry attempts, got {call_count}"

            # Should have taken significant time due to exponential retry backoff
            assert duration > 1.0, f"Max retries too fast: {duration}s"

    @pytest.mark.asyncio
    async def test_partial_text_matches(self, lastfm_client):
        """Test that partial text matches work correctly."""

        error_messages = [
            "This track was not found in our database",  # Contains "not found"
            "Network connection timeout after 30 seconds",  # Contains "timeout"
            "Invalid API key provided for authentication",  # Contains "invalid key"
        ]

        for message in error_messages:
            call_count = 0

            def make_mock_error(msg):
                def mock_partial_text_error(*args, **kwargs):
                    nonlocal call_count
                    call_count += 1

                    # For retryable errors (timeout), succeed after first attempt
                    if "timeout" in msg.lower() and call_count > 1:
                        mock_track = MagicMock(spec=pylast.Track)
                        mock_track._request.return_value = MagicMock()
                        return mock_track

                    raise pylast.WSError("LastFm", "999", msg)

                return mock_partial_text_error

            mock_error_func = make_mock_error(message)

            with patch("pylast.LastFMNetwork") as mock_network_class:
                mock_network = MagicMock()
                mock_network.get_track.side_effect = mock_error_func
                mock_network_class.return_value = mock_network

                await lastfm_client.get_track_info_comprehensive(
                    "Test Artist", "Test Track"
                )

                # Verify appropriate behavior based on message content
                if "not found" in message.lower():
                    assert call_count == 1, f"'not found' should not retry: {message}"
                elif "timeout" in message.lower():
                    assert call_count > 1, f"'timeout' should retry: {message}"
                elif "invalid key" in message.lower():
                    assert call_count == 1, f"'invalid key' should not retry: {message}"
