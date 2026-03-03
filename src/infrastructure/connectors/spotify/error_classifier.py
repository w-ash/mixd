"""Spotify-specific error classification for retry behavior."""

from typing import override

import httpx

from src.infrastructure.connectors._shared.error_classifier import (
    HTTPErrorClassifier,
)


class SpotifyErrorClassifier(HTTPErrorClassifier):
    """Spotify-specific error classifier leveraging HTTP base classification.

    Inherits the ``classify_error()`` template from ``HTTPErrorClassifier``.
    The only Spotify-specific rule is that a 401 containing "access token
    expired" is recoverable (token refresh has already been triggered).
    """

    @property
    @override
    def service_name(self) -> str:
        """Return service name for logging."""
        return "spotify"

    @override
    def _classify_service_error(
        self, exception: Exception
    ) -> tuple[str, str, str] | None:
        """Treat expired-token 401s as temporary (token refresh already triggered)."""
        if (
            isinstance(exception, httpx.HTTPStatusError)
            and exception.response.status_code == 401  # noqa: PLR2004
            and "access token expired" in exception.response.text.lower()
        ):
            return ("temporary", "401", "Token expired — refreshing and retrying")
        return None
