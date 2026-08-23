"""Discogs API error classification for retry behavior.

Modern hook pattern: overrides only ``service_name`` and
``_classify_service_error`` on ``HTTPErrorClassifier`` — the template
``classify_error()`` handles HTTP status dispatch, RequestError → temporary,
and text-pattern fallbacks. The hook adds the Discogs-specific rules:

- ``DiscogsAuthRequiredError`` → permanent auth error (no retry).
- 401 → permanent auth error with an actionable reconnect message: Discogs
  auth is a BYO personal access token, so a 401 means the stored token was
  revoked or mistyped and only the user can fix it.
- 429 deliberately falls through to the template's ``rate_limit`` — the
  shared retry policy honors ``Retry-After`` and brakes the shared limiter.
"""

from http import HTTPStatus
from typing import override

import httpx2

from src.domain.exceptions import DiscogsAuthRequiredError
from src.infrastructure.connectors._shared.error_classifier import (
    HTTPErrorClassifier,
)


class DiscogsErrorClassifier(HTTPErrorClassifier):
    """Discogs error classifier on the shared HTTP classification template."""

    @property
    @override
    def service_name(self) -> str:
        """Service name for logging."""
        return "discogs"

    @override
    def _classify_service_error(
        self, exception: Exception
    ) -> tuple[str, str, str] | None:
        """Discogs-specific classification; None falls through to the template."""
        if isinstance(exception, DiscogsAuthRequiredError):
            return ("permanent", "auth", str(exception))

        if (
            isinstance(exception, httpx2.HTTPStatusError)
            and exception.response.status_code == HTTPStatus.UNAUTHORIZED
        ):
            return (
                "permanent",
                "auth",
                (
                    "Discogs rejected the personal access token (401) — "
                    "reconnect Discogs from the Integrations page, or check "
                    "that your personal access token is still valid."
                ),
            )

        return None
