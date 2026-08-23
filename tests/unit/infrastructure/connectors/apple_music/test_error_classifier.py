"""Unit tests for the Apple Music error classifier (modern hook pattern).

The classifier overrides only ``_classify_service_error`` on
``HTTPErrorClassifier``; everything else (status dispatch, RequestError →
temporary, text-pattern fallback) comes from the shared template. Covers the
Apple-specific rules verified against the live API (probe 2026-08-22):

- 403 on a ``/v1/me/*`` request → permanent auth (user-token rejection).
  Apple rejects a dead or invalid Music User Token with 403 and the body
  ``{code: "40300", title: "Forbidden", detail: "Invalid authentication"}``
  — there is no TOKEN_EXPIRED-style string to match on.
- 403 on a catalog request (no Music User Token carried) → permanent ``403``
  from the template, never the ``auth`` arm.
- 401 → developer-token (instance) problem; classified by the template as
  permanent ``401``, never the user-token ``auth`` arm.
- Messages explicitly naming the developer token → permanent ``token``.
- x-rate-limit signals → rate_limit; ``AppleMusicAuthRequiredError`` →
  permanent auth.
"""

import json

import httpx2
import pytest

from src.domain.exceptions import AppleMusicAuthRequiredError
from src.infrastructure.connectors.apple_music.error_classifier import (
    AppleMusicErrorClassifier,
    indicates_user_token_rejection,
)
from src.infrastructure.connectors.apple_music.models import AppleMusicError


def make_status_error(
    status_code: int,
    body: dict[str, object] | None = None,
    text: str | None = None,
    url: str = "https://api.music.apple.com/v1/me/storefront",
) -> httpx2.HTTPStatusError:
    """HTTPStatusError with a real response carrying a JSON:API body."""
    request = httpx2.Request("GET", url)
    if body is not None:
        response = httpx2.Response(status_code, request=request, json=body)
    else:
        response = httpx2.Response(status_code, request=request, text=text or "")
    return httpx2.HTTPStatusError(
        f"HTTP {status_code}", request=request, response=response
    )


def invalid_mut_error_body() -> dict[str, object]:
    """The VERBATIM body Apple returned for a dead Music User Token.

    Captured live 2026-08-22 (bad_mut_error.json): valid developer token +
    invalid MUT → HTTP 403 with this envelope.
    """
    return {
        "errors": [
            {
                "code": "40300",
                "detail": "Invalid authentication",
                "id": "JXGSMLML5NSKKEUMZQFFIY7O4Y",
                "status": "403",
                "title": "Forbidden",
            }
        ]
    }


@pytest.fixture
def classifier() -> AppleMusicErrorClassifier:
    return AppleMusicErrorClassifier()


class TestServiceName:
    def test_service_name(self, classifier: AppleMusicErrorClassifier):
        assert classifier.service_name == "apple_music"


class TestUserTokenRejection:
    def test_403_with_real_invalid_auth_body_is_permanent_auth(
        self, classifier: AppleMusicErrorClassifier
    ):
        error_type, error_code, detail = classifier.classify_error(
            make_status_error(403, body=invalid_mut_error_body())
        )

        assert error_type == "permanent"
        assert error_code == "auth"
        assert detail == "Invalid authentication"

    def test_403_with_unrelated_body_is_still_permanent_auth(
        self, classifier: AppleMusicErrorClassifier
    ):
        # ANY 403 counts as a user-token rejection: Apple uses 403 for MUT
        # problems and 401 for developer-token problems, so there is no
        # innocent 403 to preserve as generic.
        error_type, error_code, _ = classifier.classify_error(
            make_status_error(
                403,
                body={"errors": [{"title": "Forbidden", "detail": "Not allowed"}]},
            )
        )

        assert error_type == "permanent"
        assert error_code == "auth"

    def test_403_with_unparseable_body_is_permanent_auth(
        self, classifier: AppleMusicErrorClassifier
    ):
        error_type, error_code, _ = classifier.classify_error(
            make_status_error(403, text="<html>Forbidden</html>")
        )

        assert error_type == "permanent"
        assert error_code == "auth"

    def test_auth_required_exception_is_permanent_auth(
        self, classifier: AppleMusicErrorClassifier
    ):
        error_type, error_code, _ = classifier.classify_error(
            AppleMusicAuthRequiredError()
        )

        assert error_type == "permanent"
        assert error_code == "auth"


class TestCatalogForbidden:
    """403s on requests that carried no Music User Token are not reauth."""

    def test_catalog_403_is_permanent_but_not_user_auth(
        self, classifier: AppleMusicErrorClassifier
    ):
        # The MUT rides only on /v1/me/* requests: a catalog 403 cannot be a
        # user-token rejection, so it must not take the "auth" arm (which
        # status surfaces read as "prompt the user to re-authorize").
        error_type, error_code, _ = classifier.classify_error(
            make_status_error(
                403,
                body=invalid_mut_error_body(),
                url="https://api.music.apple.com/v1/catalog/us/songs",
            )
        )

        assert error_type == "permanent"
        assert error_code == "403"


class TestDeveloperToken:
    def test_401_is_permanent_but_not_user_auth(
        self, classifier: AppleMusicErrorClassifier
    ):
        # 401 rejects the developer token — an instance problem the user
        # cannot fix by re-authorizing, so it must never take the "auth" arm.
        error_type, error_code, _ = classifier.classify_error(
            make_status_error(
                401, body={"errors": [{"title": "Unauthorized", "status": "401"}]}
            )
        )

        assert error_type == "permanent"
        assert error_code == "401"

    def test_developer_token_message_is_permanent(
        self, classifier: AppleMusicErrorClassifier
    ):
        error_type, error_code, _ = classifier.classify_error(
            Exception("Invalid developer token supplied")
        )

        assert error_type == "permanent"
        assert error_code == "token"

    def test_403_body_naming_developer_token_is_permanent_token(
        self, classifier: AppleMusicErrorClassifier
    ):
        # The one 403 exempted from the user-token arm: a body that
        # explicitly names the developer token.
        exc = make_status_error(
            403,
            body={
                "errors": [{"title": "Forbidden", "detail": "Invalid developer token"}]
            },
        )

        error_type, error_code, _ = classifier.classify_error(exc)

        assert error_type == "permanent"
        assert error_code == "token"


class TestIndicatesUserTokenRejection:
    def test_real_envelope_indicates_rejection(self):
        error = AppleMusicError.model_validate({
            "code": "40300",
            "detail": "Invalid authentication",
            "status": "403",
            "title": "Forbidden",
        })

        assert indicates_user_token_rejection(error) is True

    def test_missing_body_still_indicates_rejection(self):
        # Any 403 counts — an absent or unparseable body does not exempt it.
        assert indicates_user_token_rejection(None) is True

    def test_developer_token_body_does_not_indicate_rejection(self):
        error = AppleMusicError.model_validate({
            "title": "Forbidden",
            "detail": "Invalid developer token",
        })

        assert indicates_user_token_rejection(error) is False


class TestRateLimits:
    def test_429_is_rate_limit(self, classifier: AppleMusicErrorClassifier):
        error_type, _, _ = classifier.classify_error(make_status_error(429))

        assert error_type == "rate_limit"

    def test_x_rate_limit_body_is_rate_limit(
        self, classifier: AppleMusicErrorClassifier
    ):
        exc = Exception(
            "Request failed: " + json.dumps({"message": "x-rate-limit exceeded"})
        )

        error_type, _, _ = classifier.classify_error(exc)

        assert error_type == "rate_limit"
