"""Spotify-specific error classification for retry behavior."""

from http import HTTPStatus
from typing import cast, override

import httpx2

from src.config import get_logger
from src.domain.exceptions import SpotifyReauthRequiredError
from src.infrastructure.connectors._shared.error_classifier import (
    HTTPErrorClassifier,
)
from src.infrastructure.connectors._shared.http_client import parse_json_body

logger = get_logger(__name__).bind(service="spotify")


def is_quota_exhausted_response(response: httpx2.Response) -> bool:
    """Check a 429 body for ``reason: "QUOTA_EXCEEDED"`` (PDR-003).

    Spotify error bodies are usually ``{"error": {...}}``, but the reason
    key is accepted at either nesting level. Any body that doesn't parse
    as a JSON object is treated as "not quota" (falls through to the
    ordinary rate-limit path) rather than raising.

    Module-level, not classifier-private: the client's suppression seam asks
    the same question to decide whether an exhausted-retries 429 escapes as
    ``SpotifyQuotaExhaustedError`` instead of dissolving into ``None``.
    """
    body = parse_json_body(response)
    if body is None:
        return False
    if body.get("reason") == "QUOTA_EXCEEDED":
        return True
    error = body.get("error")
    return (
        isinstance(error, dict)
        and cast("dict[object, object]", error).get("reason") == "QUOTA_EXCEEDED"
    )


class SpotifyErrorClassifier(HTTPErrorClassifier):
    """Spotify-specific error classifier leveraging HTTP base classification.

    Inherits the ``classify_error()`` template from ``HTTPErrorClassifier``.
    Spotify-specific rules:

    - A 401 containing "access token expired" is recoverable (token refresh
      has already been triggered).
    - A 429 whose body carries ``reason: "QUOTA_EXCEEDED"`` (PDR-003) is the
      pooled per-developer-account quota running out, not ordinary rate
      limiting — it does not clear on ``Retry-After`` timescales, so it is
      classified permanent to skip the retry/backoff loop entirely. A plain
      429 (no body, an unparseable body, or a different reason) falls through
      to the template's ``rate_limit`` mapping unchanged.
    """

    @property
    @override
    def service_name(self) -> str:
        """Service name for logging."""
        return "spotify"

    @override
    def _classify_service_error(
        self, exception: Exception
    ) -> tuple[str, str, str] | None:
        """Recognize expired refresh grants, token-expiry 401s, and
        quota-exhaustion 429s before the generic status-code mapping runs.
        """
        if isinstance(exception, SpotifyReauthRequiredError):
            # The refresh grant is dead and the token already deleted —
            # no retry can ever succeed (mirrors the Apple Music classifier).
            return ("permanent", "auth", str(exception))

        if not isinstance(exception, httpx2.HTTPStatusError):
            return None

        status = exception.response.status_code
        if (
            status == HTTPStatus.UNAUTHORIZED
            and "access token expired" in exception.response.text.lower()
        ):
            return ("temporary", "401", "Token expired — refreshing and retrying")

        if status == HTTPStatus.TOO_MANY_REQUESTS and is_quota_exhausted_response(
            exception.response
        ):
            # PDR-003 decide-by trigger (a): a 429 whose body carries
            # reason=QUOTA_EXCEEDED is the pooled developer-account quota
            # emptying out — Spotify does not clear this on Retry-After
            # timescales, so treating it like an ordinary rate limit would
            # retry into a wall. Distinguishable in prod logs from plain 429s
            # (see PDR-003: docs/decisions/PDR-003-spotify-dev-mode-batch-endpoints.md).
            logger.warning(
                "Spotify 429 carries reason=QUOTA_EXCEEDED — pooled "
                "developer-account quota exhausted; will not clear on "
                "Retry-After timescales; see PDR-003 "
                "(docs/decisions/PDR-003-spotify-dev-mode-batch-endpoints.md)",
            )
            return (
                "permanent",
                "429",
                (
                    "Quota exceeded — PDR-003: developer-account quota "
                    "exhausted; will not clear on Retry-After"
                ),
            )

        return None
