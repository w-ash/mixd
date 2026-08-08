"""Tests for the Retry-After-aware wait strategy in the shared retry policies.

Pins the v0.10.2.2 fix: a 429's ``Retry-After`` header is honored (capped)
instead of blindly hammering exponential backoff inside the server's stated
rate-limit window — the failure mode that killed a prod GDPR history import
after six burned attempts.
"""

from unittest.mock import MagicMock

import httpx2
import pytest
from tenacity import wait_fixed

from src.infrastructure.connectors._shared.retry_policies import (
    RetryAfterWait,
    RetryConfig,
    RetryPolicyFactory,
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


class TestCreatePolicyWiring:
    def test_policy_wait_is_retry_after_aware(self):
        policy = RetryPolicyFactory.create_policy(
            RetryConfig(
                service_name="spotify",
                classifier=SpotifyErrorClassifier(),
                max_attempts=3,
                wait_multiplier=1.0,
                wait_max=60.0,
            )
        )

        assert isinstance(policy.wait, RetryAfterWait)
        assert policy.wait.cap == 60.0
