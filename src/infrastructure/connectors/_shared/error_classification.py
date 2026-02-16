"""Generalized error classification system for connector error handling.

Provides pluggable error classification that can be customized per service
while maintaining consistent retry behavior patterns across all connectors.
"""

from abc import ABC, abstractmethod
from typing import Protocol

from src.config import get_logger
from src.config.constants import HTTPStatus as HTTPStatusCode

logger = get_logger(__name__).bind(service="connectors")


class ErrorClassifier(Protocol):
    """Protocol for service-specific error classifiers."""

    def classify_error(self, exception: Exception) -> tuple[str, str, str]:
        """Classify error for retry behavior.

        Returns:
            Tuple of (error_type, error_code, error_description)
            error_type: "permanent", "temporary", "rate_limit", "not_found", "unknown"
        """
        ...


class HTTPErrorClassifier(ABC):
    """Base classifier with shared HTTP status code and text pattern logic.

    This class provides common classification logic for HTTP-based APIs
    (Spotify, Apple Music, etc.). Service-specific classifiers can inherit
    from this class and override/extend methods as needed.

    The classification hierarchy:
    1. HTTP status codes (most reliable)
    2. Text patterns in error messages (fallback)
    3. Service-specific patterns (in subclasses)
    """

    @property
    @abstractmethod
    def service_name(self) -> str:
        """Service name for logging (override in subclass)."""
        ...

    def classify_http_status(
        self, status: int | None, error_msg: str
    ) -> tuple[str, str, str] | None:
        """Classify HTTP status codes into error categories.

        Args:
            status: HTTP status code (e.g., 404, 500)
            error_msg: Error message for context (reserved for future use in
                       service-specific classification)

        Returns:
            (error_type, error_code, error_description) or None if not HTTP error

        Categories:
            - permanent: 4xx client errors (except 404, 429)
            - temporary: 5xx server errors
            - rate_limit: 429 Too Many Requests
            - not_found: 404 Not Found
        """
        _ = error_msg  # Reserved for future use in enhanced classification
        if status is None:
            return None

        # Special cases with specific categories
        match status:
            case 404:  # Not Found
                return ("not_found", "404", "Not Found - resource doesn't exist")
            case 429:  # Too Many Requests
                return (
                    "rate_limit",
                    "429",
                    "Rate limit - too many requests",
                )

            # Common client errors (4xx) - permanent
            case 400:
                return ("permanent", "400", "Bad Request - malformed request")
            case 401:
                return (
                    "permanent",
                    "401",
                    "Unauthorized - invalid or expired token",
                )
            case 403:
                return (
                    "permanent",
                    "403",
                    "Forbidden - insufficient permissions",
                )

            # Common server errors (5xx) - temporary
            case 500:
                return (
                    "temporary",
                    "500",
                    "Internal Server Error - temporary issue",
                )
            case 502:
                return (
                    "temporary",
                    "502",
                    "Bad Gateway - upstream service issue",
                )
            case 503:
                return (
                    "temporary",
                    "503",
                    "Service Unavailable - temporary outage",
                )
            case 504:
                return (
                    "temporary",
                    "504",
                    "Gateway Timeout - upstream timeout",
                )

        # Generic 4xx client errors (permanent)
        if HTTPStatusCode.CLIENT_ERROR_MIN <= status < HTTPStatusCode.CLIENT_ERROR_MAX:
            return (
                "permanent",
                str(status),
                f"Client error {status}",
            )

        # Generic 5xx server errors (temporary)
        if (
            HTTPStatusCode.INTERNAL_SERVER_ERROR
            <= status
            < HTTPStatusCode.SERVER_ERROR_MAX
        ):
            return (
                "temporary",
                str(status),
                f"Server error {status}",
            )

        # Not an HTTP error status (e.g., 2xx success, 3xx redirect, or invalid)
        return None

    def classify_text_patterns(self, error_str: str) -> tuple[str, str, str] | None:
        """Classify errors based on text patterns in error messages.

        This provides fallback classification when HTTP status isn't available
        or for more specific error detection.

        Args:
            error_str: Error message text

        Returns:
            (error_type, error_code, error_description) or None if no pattern matches
        """
        error_lower = error_str.lower()

        # Rate limit patterns - highest priority for text-based detection
        if any(
            pattern in error_lower
            for pattern in ["rate limit", "rate_limit", "too many", "quota", "throttle"]
        ):
            return ("rate_limit", "text", "Rate limit detected from response text")

        # Network and connection errors - temporary
        if any(
            pattern in error_lower
            for pattern in ["timeout", "connection", "network", "dns", "ssl"]
        ):
            return ("temporary", "network", "Network or connection error")

        # Authentication/authorization errors - permanent
        if any(
            pattern in error_lower
            for pattern in [
                "unauthorized",
                "forbidden",
                "invalid token",
                "invalid api key",
                "authentication failed",
                "token expired",
                "expired token",
                "invalid access",
                "has expired",
                "invalid_grant",  # OAuth error
                "invalid_client",  # OAuth error
                "access_denied",  # OAuth error
            ]
        ):
            return ("permanent", "auth", "Authentication or authorization error")

        # Not found patterns
        if any(
            pattern in error_lower
            for pattern in [
                "not found",
                "not_found",
                "does not exist",
                "no such",
                "invalid id",
            ]
        ):
            return ("not_found", "text", "Resource not found")

        # Temporary service issues
        if any(
            pattern in error_lower
            for pattern in [
                "service temporarily unavailable",
                "server error",
                "server_error",
                "internal error",
                "try again",
                "temporarily",
                "unavailable",
            ]
        ):
            return ("temporary", "text", "Service temporarily unavailable")

        # No pattern matched
        return None


def classify_unknown_error(exception: Exception) -> tuple[str, str, str]:
    """Classify generic exceptions as unknown errors.

    Used as fallback for connectors without a custom error classifier.
    """
    return ("unknown", "N/A", str(exception))
