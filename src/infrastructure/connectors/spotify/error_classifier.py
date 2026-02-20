"""Spotify-specific error classification for retry behavior."""

from typing import override

import httpx

from src.infrastructure.connectors._shared.error_classification import (
    HTTPErrorClassifier,
)


class SpotifyErrorClassifier(HTTPErrorClassifier):
    """Spotify-specific error classifier leveraging HTTP base classification.

    Inherits HTTP status code and text pattern classification from HTTPErrorClassifier,
    mapping httpx exceptions to retry categories.
    """

    @property
    @override
    def service_name(self) -> str:
        """Return service name for logging."""
        return "spotify"

    def classify_error(self, exception: Exception) -> tuple[str, str, str]:
        """Classify Spotify API errors for proper retry behavior.

        Uses parent class HTTP classification with httpx exception handling.

        Args:
            exception: The exception to classify

        Returns:
            Tuple of (error_type, error_code, error_description)
            error_type: "permanent", "temporary", "rate_limit", "not_found", "unknown"
        """
        # httpx HTTP errors (4xx, 5xx — response was received)
        if isinstance(exception, httpx.HTTPStatusError):
            status = exception.response.status_code

            # 401 "The access token expired" is recoverable: force_refresh()
            # has already been called before this exception was re-raised, so
            # the next retry attempt will use a fresh token. Other 401s (wrong
            # credentials, revoked app access) remain permanent.
            if (
                status == 401  # noqa: PLR2004
                and "access token expired" in exception.response.text.lower()
            ):
                return ("temporary", "401", "Token expired — refreshing and retrying")

            error_msg = str(exception)
            if result := self.classify_http_status(status, error_msg):
                return result
            if result := self.classify_text_patterns(error_msg.lower()):
                return result
            return ("unknown", str(status), error_msg)

        # httpx network/connection errors (no response received)
        if isinstance(exception, httpx.RequestError):
            error_str = str(exception).lower()
            if result := self.classify_text_patterns(error_str):
                return result
            return ("temporary", "network", str(exception))

        # Non-httpx exceptions (programming errors, etc.)
        error_str = str(exception).lower()
        if result := self.classify_text_patterns(error_str):
            return result
        return ("unknown", "N/A", str(exception))
