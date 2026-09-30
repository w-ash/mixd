"""Unit tests for SpotifyErrorClassifier.

The generic status-code and text-pattern mapping belongs to the shared
template (``_shared/test_http_error_classifier.py``). These tests cover what
the Spotify hook adds, and that it falls through to the template otherwise:
- Unknown errors and unknown status codes reach the template's fallbacks
- Auth and temporary-service text patterns: these pin keywords (OAuth error
  codes, "token expired", "internal error") the shared suite does not yet cover
- The expired-access-token 401 is temporary; every other 401 stays permanent
- Quota-429 discrimination (PDR-003): QUOTA_EXCEEDED bodies are permanent and
  never enter the Retry-After backoff loop; plain 429s keep today's behavior.
"""

import json
from unittest.mock import MagicMock, patch

import httpx2
import pytest
import structlog.testing
from tenacity import wait_none

from src.infrastructure.connectors._shared.retry_policies import (
    RetryConfig,
    RetryPolicyFactory,
)
from src.infrastructure.connectors.spotify.error_classifier import (
    SpotifyErrorClassifier,
)


def make_http_error(
    status_code: int,
    message: str = "",
    *,
    json_body: object = None,
    text_body: str | None = None,
    headers: dict[str, str] | None = None,
) -> httpx2.HTTPStatusError:
    """Create an httpx2.HTTPStatusError with the given status code."""
    request = httpx2.Request("GET", "https://api.spotify.com/v1/tracks")
    if json_body is not None:
        content = json.dumps(json_body).encode()
    elif text_body is not None:
        content = text_body.encode()
    else:
        content = b""
    response = httpx2.Response(
        status_code, headers=headers or {}, content=content, request=request
    )
    return httpx2.HTTPStatusError(
        message or f"HTTP {status_code}", request=request, response=response
    )


class TestSpotifyErrorClassifier:
    """Unit tests for SpotifyErrorClassifier classification logic."""

    @pytest.fixture
    def classifier(self):
        """Create a SpotifyErrorClassifier instance."""
        return SpotifyErrorClassifier()

    @pytest.mark.parametrize(
        "error_message",
        [
            "invalid access token",
            "token expired",
            "unauthorized request",
            "invalid_grant error",
            "invalid_client provided",
            "access_denied by user",
        ],
    )
    def test_authentication_text_patterns(self, classifier, error_message):
        """Test that authentication error text patterns are classified as permanent."""
        exception = Exception(error_message)

        error_type, error_code, error_description = classifier.classify_error(exception)

        assert error_type == "permanent"
        assert error_code == "auth"
        assert (
            "authentication" in error_description.lower()
            or "authorization" in error_description.lower()
        )

    @pytest.mark.parametrize(
        "error_message",
        [
            "Service temporarily unavailable",
            "Internal server error occurred",
            "Please try again later",
            "Service is temporarily down",
            "System unavailable for maintenance",
            "Internal error - please retry",
        ],
    )
    def test_temporary_service_text_patterns(self, classifier, error_message):
        """Test that temporary service error text patterns are classified correctly."""
        exception = Exception(error_message)

        error_type, error_code, error_description = classifier.classify_error(exception)

        assert error_type == "temporary"
        assert error_code == "text"
        assert (
            "temporarily" in error_description.lower()
            or "unavailable" in error_description.lower()
        )

    def test_non_network_exception_unknown(self, classifier):
        """Test that unknown non-httpx2 exceptions are classified as unknown."""
        exception = ValueError("Unexpected value error")

        error_type, error_code, error_description = classifier.classify_error(exception)

        assert error_type == "unknown"
        assert error_code == "N/A"
        assert error_description == str(exception)

    def test_unknown_http_status_code(self, classifier):
        """Test handling of unknown HTTP status codes."""
        exception = make_http_error(999, "Unknown HTTP status")

        error_type, error_code, _error_description = classifier.classify_error(
            exception
        )

        assert error_type == "unknown"
        assert error_code == "999"


class TestExpiredAccessToken401:
    """Spotify's own 401 rule: an expired access token is retried, not given up.

    The bearer-auth flow has already refreshed the token by the time the retry
    policy asks, so the next attempt carries a fresh one. Any other 401 (a
    revoked or malformed token) cannot be fixed by a retry and stays permanent.
    """

    @pytest.fixture
    def classifier(self):
        return SpotifyErrorClassifier()

    def test_expired_token_401_is_temporary(self, classifier):
        exception = make_http_error(
            401,
            json_body={"error": {"status": 401, "message": "The access token expired"}},
        )

        error_type, error_code, _ = classifier.classify_error(exception)

        assert (error_type, error_code) == ("temporary", "401")

    @pytest.mark.parametrize(
        "json_body",
        [
            pytest.param(None, id="no-body"),
            pytest.param(
                {"error": {"status": 401, "message": "Invalid access token"}},
                id="invalid-token",
            ),
        ],
    )
    def test_any_other_401_stays_permanent(self, classifier, json_body):
        exception = make_http_error(401, json_body=json_body)

        error_type, error_code, _ = classifier.classify_error(exception)

        assert (error_type, error_code) == ("permanent", "401")


class TestQuota429Discrimination:
    """PDR-003: pooled developer-account quota exhaustion (reason=QUOTA_EXCEEDED)
    is a distinct 429 shape from ordinary rate limiting — it must classify as
    permanent so it never enters the Retry-After backoff loop, since quota
    exhaustion does not clear on Retry-After timescales.
    """

    @pytest.fixture
    def classifier(self):
        return SpotifyErrorClassifier()

    @pytest.fixture(autouse=True)
    def _fresh_module_logger(self, monkeypatch: pytest.MonkeyPatch):
        """Give the classifier module an uncached logger per test.

        ``cache_logger_on_first_use=True`` pins a logger's processor chain
        at its first emission, so ``capture_logs`` sees nothing when an
        earlier test in the worker already used the module logger.
        """
        from src.infrastructure.connectors.spotify import error_classifier

        fresh = structlog.get_logger(error_classifier.__name__).bind(service="spotify")
        monkeypatch.setattr(error_classifier, "logger", fresh)

    def test_quota_exceeded_top_level_reason_is_permanent(self, classifier):
        exception = make_http_error(429, json_body={"reason": "QUOTA_EXCEEDED"})

        error_type, error_code, error_description = classifier.classify_error(exception)

        assert error_type == "permanent"
        assert error_code == "429"
        assert "PDR-003" in error_description

    def test_quota_exceeded_nested_under_error_is_permanent(self, classifier):
        exception = make_http_error(
            429,
            json_body={"error": {"status": 429, "reason": "QUOTA_EXCEEDED"}},
        )

        error_type, error_code, error_description = classifier.classify_error(exception)

        assert error_type == "permanent"
        assert error_code == "429"
        assert "PDR-003" in error_description

    @pytest.mark.parametrize(
        ("json_body", "text_body"),
        [
            pytest.param(None, None, id="no-body"),
            pytest.param({"reason": "SOMETHING_ELSE"}, None, id="different-reason"),
            pytest.param(None, "not json at all {{{", id="unparseable-body"),
            pytest.param(["QUOTA_EXCEEDED"], None, id="non-dict-body"),
        ],
    )
    def test_plain_429_falls_through_to_rate_limit(
        self, classifier, json_body: object, text_body: str | None
    ):
        # Anything short of a parsed QUOTA_EXCEEDED reason is an ordinary 429.
        exception = make_http_error(429, json_body=json_body, text_body=text_body)

        error_type, error_code, _error_description = classifier.classify_error(
            exception
        )

        assert error_type == "rate_limit"
        assert error_code == "429"

    def test_quota_exceeded_emits_warning_naming_pdr_003(self, classifier):
        exception = make_http_error(429, json_body={"reason": "QUOTA_EXCEEDED"})

        with structlog.testing.capture_logs() as captured:
            _ = classifier.classify_error(exception)

        warnings = [e for e in captured if e.get("log_level") == "warning"]
        assert len(warnings) == 1
        assert "PDR-003" in warnings[0]["event"]

    def test_plain_429_emits_no_quota_warning(self, classifier):
        exception = make_http_error(429)

        with structlog.testing.capture_logs() as captured:
            _ = classifier.classify_error(exception)

        assert captured == []


class TestQuota429RetryIntegration:
    """Quota 429s must never enter the tenacity retry loop or pause the
    connector's rate limiter — the classification is permanent, which
    ``create_error_classifier_retry`` (via ``RetryPolicyFactory``) skips.
    Plain 429s keep today's behavior: retried, with Retry-After honored.
    """

    def _policy(self, wait_max: float = 17.0):
        config = RetryConfig(
            service_name="spotify",
            classifier=SpotifyErrorClassifier(),
            max_attempts=3,
            wait_multiplier=0.01,
            wait_max=wait_max,
        )
        policy = RetryPolicyFactory.create_policy(config)
        policy.wait = wait_none()
        return policy

    async def test_quota_429_never_retries_and_never_pauses(self):
        limiter = MagicMock()
        call_count = 0

        async def flaky() -> None:
            nonlocal call_count
            call_count += 1
            raise make_http_error(429, json_body={"reason": "QUOTA_EXCEEDED"})

        policy = self._policy()

        with patch(
            "src.infrastructure.connectors._shared.retry_policies."
            "get_connector_rate_limiter",
            return_value=limiter,
        ):
            with pytest.raises(httpx2.HTTPStatusError):
                await policy(flaky)

        assert call_count == 1
        limiter.pause_for.assert_not_called()

    async def test_plain_429_retries_and_honors_retry_after(self):
        limiter = MagicMock()
        call_count = 0

        async def flaky() -> str:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise make_http_error(429, headers={"Retry-After": "3"})
            return "ok"

        policy = self._policy()

        with patch(
            "src.infrastructure.connectors._shared.retry_policies."
            "get_connector_rate_limiter",
            return_value=limiter,
        ):
            result = await policy(flaky)

        assert result == "ok"
        assert call_count == 2
        limiter.pause_for.assert_called_once_with(3.0)


class TestReauthRequiredClassification:
    """An expired Spotify refresh grant (v0.11.2) can never succeed on retry —
    the token was already deleted at the detection site, so the classifier
    must mark ``SpotifyReauthRequiredError`` permanent (no retries).
    """

    @pytest.fixture
    def classifier(self):
        return SpotifyErrorClassifier()

    def test_reauth_required_error_is_permanent(self, classifier):
        from src.domain.exceptions import SpotifyReauthRequiredError

        error_type, _error_code, _description = classifier.classify_error(
            SpotifyReauthRequiredError()
        )

        assert error_type == "permanent"
