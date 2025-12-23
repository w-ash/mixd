"""Spotify-specific error classification for retry behavior."""

from __future__ import annotations

import spotipy

from src.infrastructure.connectors._shared.error_classification import (
    HTTPErrorClassifier,
)


class SpotifyErrorClassifier(HTTPErrorClassifier):
    """Spotify-specific error classifier leveraging HTTP base classification.

    Inherits HTTP status code and text pattern classification from HTTPErrorClassifier,
    adding only Spotify-specific exception handling and error detail parsing.
    """

    @property
    def service_name(self) -> str:
        """Return service name for logging."""
        return "spotify"

    def classify_error(self, exception: Exception) -> tuple[str, str, str]:
        """Classify Spotify API errors for proper retry behavior.

        Uses parent class HTTP classification with Spotify-specific handling for:
        - SpotifyException with HTTP status codes
        - Non-Spotify network exceptions
        - Spotify OAuth error detail parsing

        Args:
            exception: The exception to classify

        Returns:
            Tuple of (error_type, error_code, error_description)
            error_type: "permanent", "temporary", "rate_limit", "not_found", "unknown"
        """
        # Handle non-Spotify exceptions (network errors, etc.)
        if not isinstance(exception, spotipy.SpotifyException):
            error_str = str(exception).lower()

            # Try base class text pattern classification
            if result := self.classify_text_patterns(error_str):
                return result

            # Fallback to unknown
            return ("unknown", "N/A", str(exception))

        # Extract HTTP status code and error details from SpotifyException
        http_status = getattr(exception, "http_status", None)
        error_msg = str(exception)

        # Parse Spotify-specific error details
        error_details = self._parse_spotify_error_details(exception)
        error_code = error_details.get(
            "error", str(http_status) if http_status else "unknown"
        )
        error_description = error_details.get("error_description", error_msg)

        # Try HTTP status classification first (most reliable)
        if http_status and (
            result := self.classify_http_status(http_status, error_msg)
        ):
            return result

        # Fall back to text pattern classification
        error_msg_lower = error_msg.lower()
        if result := self.classify_text_patterns(error_msg_lower):
            return result

        # Default to unknown for unrecognized Spotify errors
        return ("unknown", error_code, error_description)

    def _parse_spotify_error_details(
        self, exception: spotipy.SpotifyException
    ) -> dict[str, str]:
        """Parse error details from Spotify API response.

        Spotify errors may contain additional details in the exception message
        or in structured error responses.
        """
        try:
            # Try to extract structured error information if available
            # SpotifyException sometimes includes error details in msg
            error_msg = str(exception)

            # Simple parsing - could be enhanced with JSON parsing if needed
            details = {}

            # Look for common OAuth error patterns
            if "error:" in error_msg:
                parts = error_msg.split("error:", 1)
                if len(parts) > 1:
                    error_part = parts[1].strip()
                    if "," in error_part:
                        details["error"] = error_part.split(",")[0].strip()
                    else:
                        details["error"] = error_part

            # Look for error_description
            if "error_description:" in error_msg:
                parts = error_msg.split("error_description:", 1)
                if len(parts) > 1:
                    details["error_description"] = parts[1].strip()

            return details

        except Exception:
            # If parsing fails, return empty dict
            return {}
