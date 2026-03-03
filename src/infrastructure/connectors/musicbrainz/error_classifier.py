"""MusicBrainz-specific error classification for retry behavior.

MusicBrainz API uses standard HTTP status codes with special handling
for rate limiting (503 Service Unavailable instead of 429).

The base HTTPErrorClassifier template handles everything else:
- 404 → not_found, 400-403 → permanent, 500-504 → temporary
- Network/timeout errors → temporary
- Text pattern fallback for non-httpx exceptions
"""

from typing import override

import httpx

from src.infrastructure.connectors._shared.error_classifier import (
    HTTPErrorClassifier,
)


class MusicBrainzErrorClassifier(HTTPErrorClassifier):
    """MusicBrainz error classifier — only override is 503 → rate_limit.

    MusicBrainz enforces its 1-request-per-second policy with 503 (not 429),
    so we intercept that before the base template classifies it as generic
    "temporary".
    """

    @property
    @override
    def service_name(self) -> str:
        """Return service name for logging."""
        return "musicbrainz"

    @override
    def _classify_service_error(
        self, exception: Exception
    ) -> tuple[str, str, str] | None:
        """Classify 503 as rate_limit (MusicBrainz rate enforcement)."""
        if (
            isinstance(exception, httpx.HTTPStatusError)
            and exception.response.status_code == 503  # noqa: PLR2004
        ):
            return ("rate_limit", "503", "Rate limit exceeded (1 req/sec)")
        return None
