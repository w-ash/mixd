"""Tests for the shared retry policies' 429 handling.

Pins the v0.10.2.2 fix: a 429's ``Retry-After`` header is honored (capped)
instead of blindly hammering exponential backoff inside the server's stated
rate-limit window — the failure mode that killed a prod GDPR history import
after six burned attempts. Also pins the shared brake: the same header pauses
the service's rate limiter, so concurrent callers stop spending into the window
the server just closed.
"""

from unittest.mock import MagicMock, patch

import httpx2
import pytest
from tenacity import wait_fixed

from src.infrastructure.connectors._shared.retry_policies import (
    DEFAULT_RATE_LIMIT_PAUSE_SECONDS,
    RetryAfterWait,
    RetryConfig,
    RetryPolicyFactory,
    create_tenacity_backoff_handler,
)
from src.infrastructure.connectors.spotify.error_classifier import (
    SpotifyErrorClassifier,
)

_FALLBACK_SECONDS = 3.5


def _wait() -> RetryAfterWait:
    return RetryAfterWait(fallback=wait_fixed(_FALLBACK_SECONDS), cap=60.0)


def _retry_state_with(exception: BaseException | None) -> MagicMock:
    state = MagicMock()
    if exception is None:
        state.outcome = None
    else:
        state.outcome.failed = True
        state.outcome.exception.return_value = exception
    return state


def _http_status_error(status: int, headers: dict[str, str]) -> httpx2.HTTPStatusError:
    request = httpx2.Request("GET", "https://api.spotify.com/v1/tracks")
    response = httpx2.Response(status, headers=headers, request=request)
    return httpx2.HTTPStatusError("boom", request=request, response=response)


class TestRetryAfterWait:
    def test_429_retry_after_honored_with_margin(self):
        exc = _http_status_error(429, {"Retry-After": "7"})

        assert _wait()(_retry_state_with(exc)) == 8.0

    def test_retry_after_capped(self):
        exc = _http_status_error(429, {"Retry-After": "300"})

        assert _wait()(_retry_state_with(exc)) == 60.0

    def test_429_without_header_uses_fallback(self):
        exc = _http_status_error(429, {})

        assert _wait()(_retry_state_with(exc)) == _FALLBACK_SECONDS

    def test_non_429_status_uses_fallback(self):
        exc = _http_status_error(503, {"Retry-After": "7"})

        assert _wait()(_retry_state_with(exc)) == _FALLBACK_SECONDS

    def test_non_http_error_uses_fallback(self):
        exc = httpx2.ConnectError("network down")

        assert _wait()(_retry_state_with(exc)) == _FALLBACK_SECONDS

    def test_http_date_form_uses_fallback(self):
        exc = _http_status_error(429, {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"})

        assert _wait()(_retry_state_with(exc)) == _FALLBACK_SECONDS

    def test_no_outcome_uses_fallback(self):
        assert _wait()(_retry_state_with(None)) == _FALLBACK_SECONDS

    @pytest.mark.parametrize("header", ["nan", "inf", "-inf", "infinity"])
    def test_non_finite_header_uses_fallback(self, header: str):
        # float() parses these, and NaN slips past both the floor and the cap —
        # tenacity would get an undefined or unbounded sleep.
        exc = _http_status_error(429, {"Retry-After": header})

        assert _wait()(_retry_state_with(exc)) == _FALLBACK_SECONDS

    def test_negative_header_clamps_to_floor(self):
        # Clamped to 0s, so the next attempt waits only the 1s margin.
        exc = _http_status_error(429, {"Retry-After": "-5"})

        assert _wait()(_retry_state_with(exc)) == 1.0


def _backoff_state(exception: BaseException) -> MagicMock:
    """A retry state the backoff handler can also format for logging."""
    state = _retry_state_with(exception)
    state.attempt_number = 2
    state.idle_for = 1.0
    state.seconds_since_start = 1.0
    return state


_PAUSE_CAP_SECONDS = 60.0


class TestBackoffHandlerBrakesTheLimiter:
    """A 429 pauses the whole service's limiter, not just the failing call."""

    def _run(
        self, exception: BaseException, cap: float = _PAUSE_CAP_SECONDS
    ) -> MagicMock:
        limiter = MagicMock()
        handler = create_tenacity_backoff_handler(
            SpotifyErrorClassifier(), "spotify", cap
        )
        with patch(
            "src.infrastructure.connectors._shared.retry_policies.get_connector_rate_limiter",
            return_value=limiter,
        ):
            handler(_backoff_state(exception))
        return limiter

    def test_rate_limit_pauses_for_the_retry_after_value(self):
        limiter = self._run(_http_status_error(429, {"Retry-After": "7"}))

        limiter.pause_for.assert_called_once_with(7.0)

    def test_hostile_retry_after_is_capped(self):
        # 999999 is finite, so it clears the parse guard. Uncapped it would
        # freeze the service's process-global limiter for ~11.5 days.
        limiter = self._run(_http_status_error(429, {"Retry-After": "999999"}))

        limiter.pause_for.assert_called_once_with(_PAUSE_CAP_SECONDS)

    def test_rate_limit_without_a_usable_header_pauses_for_the_default(self):
        limiter = self._run(_http_status_error(429, {}))

        limiter.pause_for.assert_called_once_with(DEFAULT_RATE_LIMIT_PAUSE_SECONDS)

    def test_non_rate_limit_error_does_not_pause(self):
        limiter = self._run(_http_status_error(503, {"Retry-After": "7"}))

        limiter.pause_for.assert_not_called()

    def test_unpaced_service_is_handled_without_a_limiter(self):
        handler = create_tenacity_backoff_handler(
            SpotifyErrorClassifier(), "spotify", _PAUSE_CAP_SECONDS
        )
        with patch(
            "src.infrastructure.connectors._shared.retry_policies.get_connector_rate_limiter",
            return_value=None,
        ):
            handler(_backoff_state(_http_status_error(429, {"Retry-After": "7"})))


class TestCreatePolicyWiring:
    _CONFIG = RetryConfig(
        service_name="spotify",
        classifier=SpotifyErrorClassifier(),
        max_attempts=3,
        wait_multiplier=1.0,
        wait_max=17.0,
    )

    def test_policy_wait_is_retry_after_aware(self):
        policy = RetryPolicyFactory.create_policy(self._CONFIG)

        assert isinstance(policy.wait, RetryAfterWait)
        assert policy.wait.cap == self._CONFIG.wait_max

    def test_limiter_pause_is_capped_by_the_policys_wait_max(self):
        """One bound per service: the pause cap is the wait cap, not a constant."""
        policy = RetryPolicyFactory.create_policy(self._CONFIG)
        before_sleep = policy.before_sleep
        assert before_sleep is not None
        limiter = MagicMock()

        with patch(
            "src.infrastructure.connectors._shared.retry_policies.get_connector_rate_limiter",
            return_value=limiter,
        ):
            before_sleep(
                _backoff_state(_http_status_error(429, {"Retry-After": "999999"}))
            )

        limiter.pause_for.assert_called_once_with(self._CONFIG.wait_max)
