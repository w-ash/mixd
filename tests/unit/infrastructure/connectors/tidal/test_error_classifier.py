"""Unit tests for the Tidal error classifier (modern hook pattern).

The classifier overrides only ``_classify_service_error`` on
``HTTPErrorClassifier``; everything else (status dispatch, RequestError →
temporary, text-pattern fallback) comes from the shared template. Tidal
publishes no rate-limit numbers, so 429 deliberately falls through to the
template's ``rate_limit`` — Retry-After is honored by the shared
``RetryAfterWait``, never an invented backoff number. 401 is the classifier's
own surface: Tidal's OAuth access token was rejected, and only the user can
fix that by reconnecting.
"""

import httpx2
import pytest

from src.domain.exceptions import TidalAuthRequiredError
from src.infrastructure.connectors.tidal.error_classifier import TidalErrorClassifier
from src.infrastructure.persistence.repositories.token_refresh_lock import (
    TokenRefreshContendedError,
)


def make_status_error(
    status_code: int,
    body: dict[str, object] | None = None,
    text: str | None = None,
    headers: dict[str, str] | None = None,
    url: str = "https://openapi.tidal.com/v2/tracks",
) -> httpx2.HTTPStatusError:
    """HTTPStatusError with a real response, optionally carrying a JSON:API body."""
    request = httpx2.Request("GET", url)
    if body is not None:
        response = httpx2.Response(
            status_code, request=request, json=body, headers=headers
        )
    else:
        response = httpx2.Response(
            status_code, request=request, text=text or "", headers=headers
        )
    return httpx2.HTTPStatusError(
        f"HTTP {status_code}", request=request, response=response
    )


@pytest.fixture
def classifier() -> TidalErrorClassifier:
    return TidalErrorClassifier()


class TestServiceName:
    def test_service_name(self, classifier: TidalErrorClassifier):
        assert classifier.service_name == "tidal"


class TestAuthClassification:
    def test_auth_required_error_is_permanent_auth(
        self, classifier: TidalErrorClassifier
    ):
        # The client raises the typed error pre-raise_for_status; the
        # classifier still owns its category (Discogs/Apple precedent) so
        # retry/give-up logging never reads a dead grant as retryable.
        error_type, error_code, _ = classifier.classify_error(
            TidalAuthRequiredError("reconnect Tidal")
        )

        assert error_type == "permanent"
        assert error_code == "auth"

    def test_refresh_contention_is_temporary(self, classifier: TidalErrorClassifier):
        # A waiter that timed out on the single-flight refresh lock lost a
        # race, nothing more — retry, never give up permanently.
        error_type, _, _ = classifier.classify_error(
            TokenRefreshContendedError("tidal")
        )

        assert error_type == "temporary"

    def test_401_is_permanent_auth_with_actionable_message(
        self, classifier: TidalErrorClassifier
    ):
        error_type, error_code, description = classifier.classify_error(
            make_status_error(401)
        )

        assert error_type == "permanent"
        assert error_code == "auth"
        assert "reconnect Tidal" in description

    def test_401_with_json_api_body_surfaces_detail(
        self, classifier: TidalErrorClassifier
    ):
        body = {
            "errors": [
                {
                    "status": "401",
                    "title": "Unauthorized",
                    "detail": "The access token expired",
                }
            ]
        }

        error_type, error_code, description = classifier.classify_error(
            make_status_error(401, body=body)
        )

        assert error_type == "permanent"
        assert error_code == "auth"
        assert "The access token expired" in description
        assert "reconnect Tidal" in description

    def test_401_with_unparseable_body_still_permanent_auth(
        self, classifier: TidalErrorClassifier
    ):
        error_type, error_code, description = classifier.classify_error(
            make_status_error(401, text="<html>Unauthorized</html>")
        )

        assert error_type == "permanent"
        assert error_code == "auth"
        assert "reconnect Tidal" in description
