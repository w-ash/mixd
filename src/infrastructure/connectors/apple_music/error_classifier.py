"""Apple Music API error classification for retry behavior."""

import json
from typing import Any

from src.config.constants import HTTPStatus
from src.infrastructure.connectors._shared.error_classification import (
    BaseErrorClassifier,
)


class AppleMusicErrorClassifier(BaseErrorClassifier):
    """Apple Music API error classifier with HTTP status and rate limit handling."""

    def classify_error(self, exception: Exception) -> tuple[str, str, str]:
        """Classify Apple Music API errors for proper retry behavior.

        Args:
            exception: The exception to classify

        Returns:
            Tuple of (error_type, error_code, error_description)
            error_type: "permanent", "temporary", "rate_limit", "not_found", "unknown"
        """
        # Handle different types of exceptions that might occur with Apple Music API
        error_str = str(exception).lower()

        # Try to extract HTTP status code if available
        http_status = self._extract_http_status(exception)

        # Parse Apple Music specific error details if available
        error_details = self._parse_apple_music_error_details(exception)
        error_code = error_details.get(
            "code", str(http_status) if http_status else "unknown"
        )
        error_description = error_details.get("detail", str(exception))

        # Classify based on HTTP status codes (Apple Music uses standard HTTP patterns)
        if http_status:
            # Client errors (4xx)
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
                    "Unauthorized - invalid developer token",
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
            elif HTTPStatus.CLIENT_ERROR_MIN <= http_status < HTTPStatus.CLIENT_ERROR_MAX:
                return (
                    "permanent",
                    str(http_status),
                    f"Client error: {error_description}",
                )

            # Server errors (5xx) - temporary
            elif http_status == HTTPStatus.INTERNAL_SERVER_ERROR:
                return ("temporary", str(http_status), "Internal Server Error")
            elif http_status == HTTPStatus.BAD_GATEWAY:
                return ("temporary", str(http_status), "Bad Gateway")
            elif http_status == HTTPStatus.SERVICE_UNAVAILABLE:
                return ("temporary", str(http_status), "Service Unavailable")
            elif http_status == HTTPStatus.GATEWAY_TIMEOUT:
                return ("temporary", str(http_status), "Gateway Timeout")
            elif HTTPStatus.INTERNAL_SERVER_ERROR <= http_status < HTTPStatus.SERVER_ERROR_MAX:
                return (
                    "temporary",
                    str(http_status),
                    f"Server error: {error_description}",
                )

        # Check for Apple Music specific error patterns

        # Rate limiting patterns (Apple Music uses X-Rate-Limit headers)
        if any(
            pattern in error_str
            for pattern in [
                "rate limit",
                "too many",
                "quota",
                "throttle",
                "x-rate-limit",
            ]
        ):
            return ("rate_limit", "text", "Rate limit detected from response")

        # Developer token issues (common with Apple Music API)
        if any(
            pattern in error_str
            for pattern in [
                "developer token",
                "invalid token",
                "token expired",
                "unauthorized",
            ]
        ):
            return ("permanent", "token", "Developer token issue")

        # Apple Music specific error codes (if we encounter them)
        # These would be based on actual Apple Music API documentation
        if "skcloudservicenetworkconnectionfailed" in error_str:
            return ("temporary", "network", "Cloud service network connection failed")

        # Not found patterns
        if any(
            pattern in error_str
            for pattern in ["not found", "does not exist", "no such", "invalid id"]
        ):
            return ("not_found", "text", "Resource not found")

        # Network and connection errors
        if any(
            pattern in error_str
            for pattern in ["timeout", "connection", "network", "dns", "ssl"]
        ):
            return ("temporary", "network", "Network or connection error")

        # Temporary service issues
        if any(
            pattern in error_str
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

        # Default to unknown for unrecognized errors
        return ("unknown", error_code, error_description)

    def _extract_http_status(self, exception: Exception) -> int | None:
        """Extract HTTP status code from various exception types."""
        # Check common attributes where HTTP status might be stored
        for attr in ["status_code", "http_status", "code", "response"]:
            if hasattr(exception, attr):
                value = getattr(exception, attr)
                if isinstance(value, int) and HTTPStatus.HTTP_STATUS_MIN <= value < HTTPStatus.SERVER_ERROR_MAX:
                    return value
                # Handle response objects that might contain status
                elif hasattr(value, "status_code") and not isinstance(value, int):
                    status = value.status_code
                    if isinstance(status, int) and HTTPStatus.HTTP_STATUS_MIN <= status < HTTPStatus.SERVER_ERROR_MAX:
                        return status

        return None

    def _parse_apple_music_error_details(self, exception: Exception) -> dict[str, Any]:
        """Parse Apple Music API error details from exception.

        Apple Music API errors typically follow JSON:API error format with
        an 'errors' array containing error objects with 'code', 'detail', etc.
        """
        try:
            error_msg = str(exception)
            details = {}

            # Try to parse JSON error response if present
            # Look for JSON in the error message
            json_start = error_msg.find("{")
            if json_start != -1:
                try:
                    json_part = error_msg[json_start:]
                    error_data = json.loads(json_part)

                    # Apple Music API uses JSON:API format
                    if error_data.get("errors"):
                        first_error = error_data["errors"][0]
                        if "code" in first_error:
                            details["code"] = first_error["code"]
                        if "detail" in first_error:
                            details["detail"] = first_error["detail"]
                        if "title" in first_error:
                            details["title"] = first_error["title"]

                except json.JSONDecodeError:
                    pass  # JSON parsing failed, continue with text parsing

            return details

        except Exception:
            # If parsing fails, return empty dict
            return {}
