"""Tidal API error classification for retry behavior.

Modern hook pattern: overrides only ``service_name`` and
``_classify_service_error`` on ``HTTPErrorClassifier`` — the template
``classify_error()`` handles HTTP status dispatch, RequestError → temporary,
and text-pattern fallbacks. The hook adds the Tidal-specific rules:

- ``TidalAuthRequiredError`` → permanent/auth (no retry): the client raises
  it pre-``raise_for_status`` on a post-replay 401, and retry/give-up
  logging must never read a dead grant as retryable.
- ``TokenRefreshContendedError`` → temporary: a waiter timed out on the
  single-flight refresh lock while another process's refresh was mid-POST —
  a retry either wins the lock or adopts the finished refresh.
- 401 → permanent/auth with an actionable reconnect message. The client's
  ``_get_json`` converts its own 401s before ``raise_for_status``, so this
  arm covers any path without that conversion (Discogs precedent: keep the
  status arm beside the client-side raise).
- The JSON:API ``errors[]`` body (Tidal's v2 API is JSON:API-shaped) is
  parsed defensively from the real ``httpx2.HTTPStatusError.response`` — an
  unparseable body never blocks the actionable 401 message, it just loses
  the upstream detail string.
- 429 deliberately falls through to the template's ``rate_limit`` — Tidal
  publishes no rate-limit numbers, so the shared retry policy honors
  ``Retry-After`` (via ``RetryAfterWait``) rather than any invented backoff.
- 5xx deliberately falls through to the template's ``temporary``.
"""

from http import HTTPStatus
from typing import cast, override

import httpx2

from src.domain.entities.shared import JsonValue
from src.domain.exceptions import TidalAuthRequiredError, TokenRefreshContendedError
from src.infrastructure.connectors._shared.error_classifier import (
    HTTPErrorClassifier,
)
from src.infrastructure.connectors._shared.http_client import (
    parse_json_body,
    response_text,
)


def first_json_api_error(response: httpx2.Response) -> dict[str, JsonValue] | None:
    """Parse the first JSON:API error object from a response body, if any.

    Defensive at every step — an absent, non-JSON, or unexpectedly-shaped
    body all read as "no error object", never raise. No Pydantic model here
    (deferred to the connector's ``models.py`` in a later packet); this is
    scaffold-only dict parsing mirroring Apple's ``first_json_api_error``.
    """
    if not response_text(response):
        return None
    body = parse_json_body(response)
    if body is None:
        return None
    errors = body.get("errors")
    if not isinstance(errors, list) or not errors:
        return None
    first = cast("object", errors[0])
    return cast("dict[str, JsonValue]", first) if isinstance(first, dict) else None


def _error_detail(error: dict[str, JsonValue] | None) -> str | None:
    """The most useful human-readable string on a JSON:API error object."""
    if error is None:
        return None
    detail = error.get("detail")
    if isinstance(detail, str) and detail:
        return detail
    title = error.get("title")
    if isinstance(title, str) and title:
        return title
    return None


class TidalErrorClassifier(HTTPErrorClassifier):
    """Tidal error classifier on the shared HTTP classification template."""

    @property
    @override
    def service_name(self) -> str:
        """Service name for logging."""
        return "tidal"

    @override
    def _classify_service_error(
        self, exception: Exception
    ) -> tuple[str, str, str] | None:
        """Tidal-specific classification; None falls through to the template."""
        if isinstance(exception, TidalAuthRequiredError):
            # The client raises this pre-raise_for_status; classifying it
            # here (Discogs/Apple precedent) keeps retry/give-up logging
            # from reading a dead grant as retryable.
            return ("permanent", "auth", str(exception))

        if isinstance(exception, TokenRefreshContendedError):
            # A waiter timed out on the single-flight refresh lock — the
            # winner is still mid-POST. Purely transient: retry.
            return ("temporary", "contended", str(exception))

        # Kept alongside the client's own pre-raise_for_status 401 →
        # TidalAuthRequiredError conversion, mirroring the Discogs
        # precedent: any 401 that reaches a classifier through a path
        # without that conversion must still read as permanent auth.
        if (
            isinstance(exception, httpx2.HTTPStatusError)
            and exception.response.status_code == HTTPStatus.UNAUTHORIZED
        ):
            error = first_json_api_error(exception.response)
            upstream_detail = _error_detail(error)
            message = upstream_detail or "Tidal rejected the access token (401)"
            return (
                "permanent",
                "auth",
                f"{message} — reconnect Tidal from the Integrations page.",
            )

        return None
