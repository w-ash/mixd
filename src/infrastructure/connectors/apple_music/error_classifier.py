"""Apple Music API error classification for retry behavior.

Modern hook pattern: overrides only ``service_name`` and
``_classify_service_error`` on ``HTTPErrorClassifier`` — the template
``classify_error()`` handles HTTP status dispatch, RequestError → temporary,
and text-pattern fallbacks. The hook adds the Apple-specific rules:

- ``AppleMusicAuthRequiredError`` → permanent auth error (no retry).
- 403 on a ``/v1/me/*`` request → permanent auth error (Music User Token
  rejected; MUTs cannot refresh, so re-authorization is the fix). Apple
  answers a dead/invalid MUT with 403 and the JSON:API body ``{code:
  "40300", title: "Forbidden", detail: "Invalid authentication"}`` — there
  is no TOKEN_EXPIRED-style marker to match on. Only ``/v1/me/*`` requests
  carry the MUT, so only they can be rejected because of it: a catalog 403
  falls through to the template's permanent ``403`` (instance problem, no
  reauth prompt). Developer-token problems arrive as 401, which the
  template classifies as permanent ``401`` (an instance-configuration
  failure, never a per-user reauth condition).
- Messages that explicitly name the developer token → permanent ``token``
  (checked before the 403 arm, so such a body opts out of the reauth read).
- ``x-rate-limit`` signals in the message/body → rate_limit.

The JSON:API ``errors[]`` body is parsed from the real
``httpx2.HTTPStatusError.response``, defensively — an unparseable body never
exempts a 403 from the user-token read.
"""

from http import HTTPStatus
from typing import override

import httpx2
from pydantic import ValidationError

from src.domain.exceptions import AppleMusicAuthRequiredError
from src.infrastructure.connectors._shared.error_classifier import (
    HTTPErrorClassifier,
)
from src.infrastructure.connectors.apple_music.models import (
    AppleMusicError,
    AppleMusicErrorResponse,
)


def _response_text(response: httpx2.Response) -> str:
    """Response body text, or empty when the body was never read (streaming)."""
    try:
        return response.text
    except RuntimeError:
        return ""


def is_music_user_token_request(request: httpx2.Request) -> bool:
    """Whether this request rode the per-user Music User Token.

    The client injects the MUT header on ``/v1/me/*`` calls only, so the
    path prefix is the discriminator. Only these requests can be rejected
    *because of* the MUT — a 403 anywhere else is an instance problem, not
    a reauth condition.
    """
    return request.url.path.startswith("/v1/me/")


def first_json_api_error(response: httpx2.Response) -> AppleMusicError | None:
    """Parse the first JSON:API error object from a response body, if any."""
    text = _response_text(response)
    if not text:
        return None
    try:
        envelope = AppleMusicErrorResponse.model_validate_json(text)
    except ValidationError:
        return None
    return envelope.errors[0] if envelope.errors else None


def indicates_user_token_rejection(error: AppleMusicError | None) -> bool:
    """Whether a MUT-bearing request's 403 reads as a rejected token.

    Apple uses 403 for MUT problems (401 covers the developer token), and
    the rejection body is ``{code: "40300", title: "Forbidden", detail:
    "Invalid authentication"}`` — no token-specific marker string exists. So
    any such 403 counts, including one whose body is absent or unparseable
    (``None``); the only opt-out is a body that explicitly names the
    developer token. Callers gate on :func:`is_music_user_token_request`
    first — a request that carried no MUT cannot have it rejected.
    """
    if error is None:
        return True
    combined = " ".join(
        part for part in (error.code, error.title, error.detail) if part
    ).lower()
    return "developer token" not in combined


class AppleMusicErrorClassifier(HTTPErrorClassifier):
    """Apple Music error classifier on the shared HTTP classification template."""

    @property
    @override
    def service_name(self) -> str:
        """Service name for logging."""
        return "apple_music"

    @override
    def _classify_service_error(
        self, exception: Exception
    ) -> tuple[str, str, str] | None:
        """Apple-specific classification; None falls through to the template."""
        if isinstance(exception, AppleMusicAuthRequiredError):
            return ("permanent", "auth", str(exception))

        error_text = str(exception).lower()
        parsed_error: AppleMusicError | None = None
        status: int | None = None
        mut_request = False
        if isinstance(exception, httpx2.HTTPStatusError):
            response = exception.response
            status = response.status_code
            if status >= HTTPStatus.INTERNAL_SERVER_ERROR:
                # Server errors classify by status (temporary) regardless of
                # whatever the body happens to mention.
                return None
            parsed_error = first_json_api_error(response)
            error_text = f"{error_text} {_response_text(response).lower()}"
            mut_request = is_music_user_token_request(exception.request)

        # Developer token problems are instance configuration — checked before
        # the 403 arm so a body explicitly naming the developer token isn't
        # misread as a per-user reauth condition.
        if "developer token" in error_text:
            return ("permanent", "token", "Developer token issue")

        # Only a 403 on a MUT-bearing (/v1/me/*) request reads as a user-token
        # rejection; a catalog 403 falls through to the template's permanent
        # ``403`` — an instance problem, never a reauth prompt.
        if (
            status == HTTPStatus.FORBIDDEN
            and mut_request
            and indicates_user_token_rejection(parsed_error)
        ):
            detail = (
                (parsed_error.detail or parsed_error.title) if parsed_error else None
            )
            return ("permanent", "auth", detail or "Music User Token rejected (403)")

        if "x-rate-limit" in error_text:
            return (
                "rate_limit",
                "text",
                "Rate limit detected from X-Rate-Limit header",
            )

        return None
