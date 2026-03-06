"""Unit tests for retry policy factory methods.

Tests focus on critical behavior preservation from backoff migration:
- Exception type filtering (the bug that was caught in code review)
- Stop conditions match original configuration
- Callback invocation (before_sleep and retry_error_callback)
"""

from unittest.mock import Mock

import pytest

from src.config import settings
from src.infrastructure.connectors._shared.retry_policies import (
    RetryConfig,
    RetryPolicyFactory,
)


class TestRetryPolicyFactory:
    """Tests for retry policy factory methods."""

    def test_spotify_policy_only_retries_spotify_exceptions(self):
        """Critical: Spotify policy must filter to httpx exception types only."""
        import httpx

        from src.infrastructure.connectors.spotify.error_classifier import (
            SpotifyErrorClassifier,
        )

        policy = RetryPolicyFactory.create_policy(
            RetryConfig(
                service_name="spotify",
                classifier=SpotifyErrorClassifier(),
                max_attempts=settings.api.spotify.retry_count,
                wait_multiplier=settings.api.spotify.retry_base_delay,
                wait_max=settings.api.spotify.retry_max_delay,
            )
        )

        # Plain Python ConnectionError (not httpx) should NOT be retried (fails type filter)
        network_error = Mock()
        network_error.outcome.failed = True
        network_error.outcome.exception.return_value = ConnectionError(
            "Network timeout"
        )

        assert policy.retry(network_error) is False

        # httpx.HTTPStatusError with permanent status (400) should also not retry (error classifier)
        req = httpx.Request("GET", "https://api.spotify.com/v1/tracks")
        resp = httpx.Response(400, request=req)
        permanent_error = Mock()
        permanent_error.outcome.failed = True
        permanent_error.outcome.exception.return_value = httpx.HTTPStatusError(
            "HTTP 400 Bad Request", request=req, response=resp
        )

        assert policy.retry(permanent_error) is False

    def test_lastfm_policy_retries_lastfm_and_httpx_errors(self):
        """Critical: Last.FM policy must filter to LastFMAPIError and httpx exceptions."""
        import httpx

        from src.infrastructure.connectors.lastfm.error_classifier import (
            LastFMErrorClassifier,
        )
        from src.infrastructure.connectors.lastfm.models import LastFMAPIError

        policy = RetryPolicyFactory.create_policy(
            RetryConfig(
                service_name="lastfm",
                classifier=LastFMErrorClassifier(),
                max_attempts=settings.api.lastfm.retry_count,
                wait_multiplier=settings.api.lastfm.retry_base_delay,
                wait_max=settings.api.lastfm.retry_max_delay,
                max_delay=settings.api.lastfm.retry_max_delay,
                service_error_types=(LastFMAPIError,),
            )
        )

        # LastFMAPIError with temporary code (11 = service offline) SHOULD be retried
        lastfm_error = Mock()
        lastfm_error.outcome.failed = True
        lastfm_error.outcome.exception.return_value = LastFMAPIError(
            11, "Service Offline"
        )

        assert policy.retry(lastfm_error) is True

        # httpx.RequestError SHOULD be retried (network failures are temporary)
        request = httpx.Request("POST", "https://ws.audioscrobbler.com/2.0")
        httpx_error = Mock()
        httpx_error.outcome.failed = True
        httpx_error.outcome.exception.return_value = httpx.ConnectError(
            "Connection refused", request=request
        )

        assert policy.retry(httpx_error) is True

        # Plain ConnectionError (not httpx) should NOT be retried (fails type filter)
        connection_error = Mock()
        connection_error.outcome.failed = True
        connection_error.outcome.exception.return_value = ConnectionError(
            "Connection failed"
        )

        assert policy.retry(connection_error) is False

    def test_musicbrainz_policy_retries_httpx_errors(self):
        """Critical: MusicBrainz retries httpx errors (native async httpx client)."""
        import httpx

        from src.infrastructure.connectors.musicbrainz.error_classifier import (
            MusicBrainzErrorClassifier,
        )

        policy = RetryPolicyFactory.create_policy(
            RetryConfig(
                service_name="musicbrainz",
                classifier=MusicBrainzErrorClassifier(),
                max_attempts=settings.api.musicbrainz.retry_count,
                wait_multiplier=settings.api.musicbrainz.retry_base_delay,
                wait_max=settings.api.musicbrainz.retry_max_delay,
                include_httpx_errors=True,
            )
        )

        # httpx network errors SHOULD be retried
        request = httpx.Request("GET", "https://musicbrainz.org/ws/2/recording")
        httpx_error = Mock()
        httpx_error.outcome.failed = True
        httpx_error.outcome.exception.return_value = httpx.ConnectError(
            "Connection refused", request=request
        )

        assert policy.retry(httpx_error) is True

        # Plain ConnectionError (not httpx) should NOT be retried (fails type filter)
        plain_error = Mock()
        plain_error.outcome.failed = True
        plain_error.outcome.exception.return_value = ConnectionError("Network issue")

        assert policy.retry(plain_error) is False

    def test_policies_have_correct_max_attempts(self):
        """Verify max attempt counts match original backoff configuration."""
        from src.infrastructure.connectors.lastfm.error_classifier import (
            LastFMErrorClassifier,
        )
        from src.infrastructure.connectors.musicbrainz.error_classifier import (
            MusicBrainzErrorClassifier,
        )
        from src.infrastructure.connectors.spotify.error_classifier import (
            SpotifyErrorClassifier,
        )

        spotify_policy = RetryPolicyFactory.create_policy(
            RetryConfig(
                service_name="spotify",
                classifier=SpotifyErrorClassifier(),
                max_attempts=settings.api.spotify.retry_count,
                wait_multiplier=settings.api.spotify.retry_base_delay,
                wait_max=settings.api.spotify.retry_max_delay,
            )
        )
        lastfm_policy = RetryPolicyFactory.create_policy(
            RetryConfig(
                service_name="lastfm",
                classifier=LastFMErrorClassifier(),
                max_attempts=settings.api.lastfm.retry_count,
                wait_multiplier=settings.api.lastfm.retry_base_delay,
                wait_max=settings.api.lastfm.retry_max_delay,
                max_delay=settings.api.lastfm.retry_max_delay,
            )
        )
        musicbrainz_policy = RetryPolicyFactory.create_policy(
            RetryConfig(
                service_name="musicbrainz",
                classifier=MusicBrainzErrorClassifier(),
                max_attempts=settings.api.musicbrainz.retry_count,
                wait_multiplier=settings.api.musicbrainz.retry_base_delay,
                wait_max=settings.api.musicbrainz.retry_max_delay,
                include_httpx_errors=True,
            )
        )

        # Spotify: settings.api.spotify.retry_count attempts
        retry_state = Mock()
        retry_state.attempt_number = settings.api.spotify.retry_count
        assert spotify_policy.stop(retry_state) is True

        # Last.FM: settings-based
        retry_state.attempt_number = settings.api.lastfm.retry_count
        assert lastfm_policy.stop(retry_state) is True

        # MusicBrainz: settings.api.musicbrainz.retry_count attempts
        retry_state.attempt_number = settings.api.musicbrainz.retry_count
        assert musicbrainz_policy.stop(retry_state) is True

    @pytest.mark.slow
    async def test_callbacks_are_invoked_during_retries(self):
        """Critical: Verify callbacks are invoked during retry attempts.

        This test ensures the fix for the retry callback bug:
        - before_sleep should fire before each retry (after failed attempts)
        - retry_error_callback should fire once when retries are exhausted

        Regression test for the issue where callbacks weren't firing because
        we were using 'after' instead of 'retry_error_callback'.
        """
        from tenacity import wait_none

        from src.infrastructure.connectors.lastfm.error_classifier import (
            LastFMErrorClassifier,
        )
        from src.infrastructure.connectors.lastfm.models import LastFMAPIError

        # Track callback invocations
        before_sleep_calls = []
        after_calls = []

        # Create policy with instrumented callbacks
        policy = RetryPolicyFactory.create_policy(
            RetryConfig(
                service_name="lastfm",
                classifier=LastFMErrorClassifier(),
                max_attempts=settings.api.lastfm.retry_count,
                wait_multiplier=settings.api.lastfm.retry_base_delay,
                wait_max=settings.api.lastfm.retry_max_delay,
                max_delay=settings.api.lastfm.retry_max_delay,
                service_error_types=(LastFMAPIError,),
            )
        )

        # Eliminate real backoff — we're testing callbacks, not timing
        policy.wait = wait_none()

        # Patch the callbacks to track invocations
        original_before_sleep = policy.before_sleep
        original_after = policy.after

        def instrumented_before_sleep(retry_state):
            before_sleep_calls.append(retry_state.attempt_number)
            if original_before_sleep:
                original_before_sleep(retry_state)

        def instrumented_after(retry_state):
            # Only track final attempts (when stop condition is met and failed)
            if retry_state.outcome and retry_state.outcome.failed:
                if retry_state.retry_object.stop(retry_state):
                    after_calls.append(retry_state.attempt_number)
            if original_after:
                original_after(retry_state)

        policy.before_sleep = instrumented_before_sleep
        policy.after = instrumented_after

        # Create a function that always fails with a retryable error
        async def failing_function():
            raise LastFMAPIError("11", "Service Offline - Try again later")

        # Execute and expect all retries to be exhausted
        # With reraise=True, tenacity should raise the original exception
        with pytest.raises(LastFMAPIError):
            await policy(failing_function)

        # Verify callbacks were invoked correctly
        # The actual number of attempts depends on which stop condition triggers first
        # (attempt count OR time limit), so we check relative behavior rather than absolute counts

        # before_sleep should fire at least once (before retry attempts)
        assert len(before_sleep_calls) >= 1, (
            f"before_sleep should fire at least once, got {len(before_sleep_calls)}"
        )

        # after callback (for final attempt) should fire exactly once at the end
        assert len(after_calls) == 1, (
            f"after callback (final attempt) should fire exactly once, "
            f"got {len(after_calls)}"
        )

        # before_sleep should fire N-1 times where N is the total attempt count
        total_attempts = after_calls[0]
        assert len(before_sleep_calls) == total_attempts - 1, (
            f"before_sleep should fire {total_attempts - 1} times "
            f"(before each retry), got {len(before_sleep_calls)}"
        )

        # Verify the attempts are sequential
        assert before_sleep_calls == list(range(1, total_attempts)), (
            f"before_sleep calls should be sequential from 1 to {total_attempts - 1}, "
            f"got {before_sleep_calls}"
        )
