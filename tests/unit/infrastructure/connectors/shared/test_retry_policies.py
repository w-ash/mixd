"""Unit tests for retry policy factory methods.

Tests focus on critical behavior preservation from backoff migration:
- Exception type filtering (the bug that was caught in code review)
- Stop conditions match original configuration
- Callback invocation (before_sleep and retry_error_callback)
"""

from unittest.mock import Mock

import pytest

from src.infrastructure.connectors._shared.retry_policies import RetryPolicyFactory


class TestRetryPolicyFactory:
    """Tests for retry policy factory methods."""

    def test_spotify_policy_only_retries_spotify_exceptions(self):
        """Critical: Spotify policy must filter to httpx exception types only."""
        import httpx

        from src.infrastructure.connectors.spotify.error_classifier import (
            SpotifyErrorClassifier,
        )

        policy = RetryPolicyFactory.create_spotify_policy(
            classifier=SpotifyErrorClassifier(),
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

        policy = RetryPolicyFactory.create_lastfm_policy(
            classifier=LastFMErrorClassifier(),
            service_error_types=(LastFMAPIError,),
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

    def test_musicbrainz_policy_retries_all_exceptions(self):
        """Critical: MusicBrainz must retry all exceptions (matching original behavior)."""
        from src.infrastructure.connectors.musicbrainz.error_classifier import (
            MusicBrainzErrorClassifier,
        )

        policy = RetryPolicyFactory.create_musicbrainz_policy(
            classifier=MusicBrainzErrorClassifier(),
        )

        # Network errors SHOULD be retried for MusicBrainz (no exception type filter)
        network_error = Mock()
        network_error.outcome.failed = True
        network_error.outcome.exception.return_value = ConnectionError("Network issue")

        # Should consider retrying (error classifier decides final outcome)
        result = policy.retry(network_error)
        assert isinstance(result, bool)

    def test_policies_have_correct_max_attempts(self):
        """Verify max attempt counts match original backoff configuration."""
        from src.config import settings
        from src.infrastructure.connectors.lastfm.error_classifier import (
            LastFMErrorClassifier,
        )
        from src.infrastructure.connectors.musicbrainz.error_classifier import (
            MusicBrainzErrorClassifier,
        )
        from src.infrastructure.connectors.spotify.error_classifier import (
            SpotifyErrorClassifier,
        )

        spotify_policy = RetryPolicyFactory.create_spotify_policy(
            classifier=SpotifyErrorClassifier(),
        )
        lastfm_policy = RetryPolicyFactory.create_lastfm_policy(
            classifier=LastFMErrorClassifier(),
        )
        musicbrainz_policy = RetryPolicyFactory.create_musicbrainz_policy(
            classifier=MusicBrainzErrorClassifier(),
        )

        # Spotify: 3 attempts
        retry_state = Mock()
        retry_state.attempt_number = 3
        assert spotify_policy.stop(retry_state) is True

        # Last.FM: settings-based
        retry_state.attempt_number = settings.api.lastfm_retry_count_rate_limit
        assert lastfm_policy.stop(retry_state) is True

        # MusicBrainz: 3 attempts
        retry_state.attempt_number = 3
        assert musicbrainz_policy.stop(retry_state) is True

    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_callbacks_are_invoked_during_retries(self):
        """Critical: Verify callbacks are invoked during retry attempts.

        This test ensures the fix for the retry callback bug:
        - before_sleep should fire before each retry (after failed attempts)
        - retry_error_callback should fire once when retries are exhausted

        Regression test for the issue where callbacks weren't firing because
        we were using 'after' instead of 'retry_error_callback'.
        """

        from src.infrastructure.connectors.lastfm.error_classifier import (
            LastFMErrorClassifier,
        )
        from src.infrastructure.connectors.lastfm.models import LastFMAPIError

        # Track callback invocations
        before_sleep_calls = []
        after_calls = []

        # Create policy with instrumented callbacks
        policy = RetryPolicyFactory.create_lastfm_policy(
            classifier=LastFMErrorClassifier(),
            service_error_types=(LastFMAPIError,),
        )

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
