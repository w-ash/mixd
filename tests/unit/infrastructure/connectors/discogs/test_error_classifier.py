"""Discogs error classification: actionable 401, template 429/5xx handling.

The classifier's own surface is the 401 → permanent/auth mapping with a
reconnect message; 429 deliberately falls through to the shared template's
``rate_limit`` (Retry-After is honored by the shared retry policy), and 5xx
stays the template's ``temporary``.
"""

import httpx2

from src.domain.exceptions import DiscogsAuthRequiredError
from src.infrastructure.connectors.discogs.error_classifier import (
    DiscogsErrorClassifier,
)


def http_status_error(status: int, headers: dict[str, str] | None = None):
    request = httpx2.Request("GET", "https://api.discogs.com/oauth/identity")
    response = httpx2.Response(status, request=request, headers=headers)
    return httpx2.HTTPStatusError(f"HTTP {status}", request=request, response=response)


class TestAuthClassification:
    def test_401_is_permanent_auth_with_actionable_message(self):
        classifier = DiscogsErrorClassifier()

        error_type, error_code, description = classifier.classify_error(
            http_status_error(401)
        )

        assert error_type == "permanent"
        assert error_code == "auth"
        assert "reconnect Discogs" in description
        assert "personal access token" in description

    def test_discogs_auth_required_error_is_permanent_auth(self):
        classifier = DiscogsErrorClassifier()

        error_type, error_code, _ = classifier.classify_error(
            DiscogsAuthRequiredError()
        )

        assert error_type == "permanent"
        assert error_code == "auth"
