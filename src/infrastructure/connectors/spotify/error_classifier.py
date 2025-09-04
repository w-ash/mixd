"""Spotify-specific error classification for retry behavior."""

import spotipy

from src.config.constants import HTTPStatus
from src.infrastructure.connectors._shared.error_classification import (
    BaseErrorClassifier,
)


class SpotifyErrorClassifier(BaseErrorClassifier):
    """Spotify-specific error classifier with HTTP status code handling."""

    def classify_error(self, exception: Exception) -> tuple[str, str, str]:
        """Classify Spotify API errors for proper retry behavior.

        Args:
            exception: The exception to classify

        Returns:
            Tuple of (error_type, error_code, error_description)
            error_type: "permanent", "temporary", "rate_limit", "not_found", "unknown"
        """
        if not isinstance(exception, spotipy.SpotifyException):
            # Handle non-Spotify exceptions (network errors, etc.)
            error_str = str(exception).lower()

            # Network and connection errors - temporary
            if any(
                pattern in error_str
                for pattern in ["timeout", "connection", "network", "dns", "ssl"]
            ):
                return ("temporary", "network", "Network or connection error")

            return ("unknown", "N/A", str(exception))

        # Extract HTTP status code and error details from SpotifyException
        http_status = getattr(exception, "http_status", None)
        error_msg = str(exception)

        # Parse error details from Spotify API response
        error_details = self._parse_spotify_error_details(exception)
        error_code = error_details.get(
            "error", str(http_status) if http_status else "unknown"
        )
        error_description = error_details.get("error_description", error_msg)

        # Classify based on HTTP status codes
        if http_status:
            # Client errors (4xx) - mostly permanent
            if http_status == HTTPStatus.BAD_REQUEST:
                return (
                    "permanent",
                    str(http_status),
                    "Bad Request - malformed request",
                )
            elif http_status == HTTPStatus.UNAUTHORIZED:
                return (
                    "permanent",
                    str(http_status),
                    "Unauthorized - invalid or expired token",
                )
            elif http_status == HTTPStatus.FORBIDDEN:
                return (
                    "permanent",
                    str(http_status),
                    "Forbidden - insufficient permissions",
                )
            elif http_status == HTTPStatus.NOT_FOUND:
                return (
                    "not_found",
                    str(http_status),
                    "Not Found - resource doesn't exist",
                )
            elif http_status == HTTPStatus.TOO_MANY_REQUESTS:
                return (
                    "rate_limit",
                    str(http_status),
                    "Too Many Requests - rate limit exceeded",
                )
            elif (
                HTTPStatus.CLIENT_ERROR_MIN <= http_status < HTTPStatus.CLIENT_ERROR_MAX
            ):
                return (
                    "permanent",
                    str(http_status),
                    f"Client error: {error_description}",
                )

            # Server errors (5xx) - temporary
            elif http_status == HTTPStatus.INTERNAL_SERVER_ERROR:
                return ("temporary", str(http_status), "Internal Server Error")
            elif http_status == HTTPStatus.BAD_GATEWAY:
                return (
                    "temporary",
                    str(http_status),
                    "Bad Gateway - upstream server issue",
                )
            elif http_status == HTTPStatus.SERVICE_UNAVAILABLE:
                return ("temporary", str(http_status), "Service Unavailable")
            elif http_status == HTTPStatus.GATEWAY_TIMEOUT:
                return ("temporary", str(http_status), "Gateway Timeout")
            elif (
                HTTPStatus.INTERNAL_SERVER_ERROR
                <= http_status
                < HTTPStatus.SERVER_ERROR_MAX
            ):
                return (
                    "temporary",
                    str(http_status),
                    f"Server error: {error_description}",
                )

        # Check for specific Spotify error patterns in the message
        error_msg_lower = error_msg.lower()

        # Rate limit patterns
        if any(
            pattern in error_msg_lower
            for pattern in ["rate limit", "too many", "quota", "throttle"]
        ):
            return ("rate_limit", "text", "Rate limit detected from response text")

        # Authentication/authorization patterns
        if any(
            pattern in error_msg_lower
            for pattern in [
                "invalid access token",
                "token expired",
                "unauthorized",
                "invalid_grant",
                "invalid_client",
                "access_denied",
            ]
        ):
            return ("permanent", "auth", "Authentication/authorization error")

        # Not found patterns
        if any(
            pattern in error_msg_lower
            for pattern in ["not found", "does not exist", "no such", "invalid id"]
        ):
            return ("not_found", "text", "Resource not found")

        # Temporary service issues
        if any(
            pattern in error_msg_lower
            for pattern in [
                "service temporarily unavailable",
                "server error",
                "internal error",
                "try again",
                "temporarily",
                "unavailable",
            ]
        ):
            return ("temporary", "text", "Service temporarily unavailable")

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
