"""Apple Music API error classification for retry behavior."""

# pyright: reportAny=false
# Legitimate Any: API response data, framework types

import json
from typing import Any, override

from src.config.constants import HTTPStatus
from src.infrastructure.connectors._shared.error_classifier import (
    HTTPErrorClassifier,
)


class AppleMusicErrorClassifier(HTTPErrorClassifier):
    """Apple Music API error classifier leveraging HTTP base classification.

    Inherits HTTP status code and text pattern classification from HTTPErrorClassifier,
    adding only Apple Music-specific error parsing and pattern detection.
    """

    @property
    @override
    def service_name(self) -> str:
        """Return service name for logging."""
        return "apple_music"

    @override
    def classify_error(self, exception: Exception) -> tuple[str, str, str]:
        """Classify Apple Music API errors for proper retry behavior.

        Uses parent class HTTP classification with Apple Music-specific handling for:
        - Flexible HTTP status extraction from various exception types
        - JSON:API error format parsing
        - Apple Music-specific error patterns (e.g., SKCloudService errors)

        Args:
            exception: The exception to classify

        Returns:
            Tuple of (error_type, error_code, error_description)
            error_type: "permanent", "temporary", "rate_limit", "not_found", "unknown"
        """
        error_str = str(exception).lower()

        # Try to extract HTTP status code if available
        http_status = self._extract_http_status(exception)

        # Parse Apple Music specific error details
        error_details = self._parse_apple_music_error_details(exception)
        error_code = error_details.get(
            "code", str(http_status) if http_status else "unknown"
        )
        error_description = error_details.get("detail", str(exception))

        # Try HTTP status classification first (most reliable)
        if http_status and (
            result := self.classify_http_status(http_status, str(exception))
        ):
            return result

        # Check for Apple Music-specific patterns before falling back to base patterns
        if apple_result := self._classify_apple_music_patterns(error_str):
            return apple_result

        # Fall back to base class text pattern classification
        if result := self.classify_text_patterns(error_str):
            return result

        # Default to unknown for unrecognized errors
        return ("unknown", error_code, error_description)

    def _classify_apple_music_patterns(
        self, error_str: str
    ) -> tuple[str, str, str] | None:
        """Classify Apple Music-specific error patterns.

        Args:
            error_str: Lowercase error string

        Returns:
            Classification tuple or None if no Apple Music pattern matches
        """
        # Apple Music specific error codes from SDK
        if "skcloudservicenetworkconnectionfailed" in error_str:
            return ("temporary", "network", "Cloud service network connection failed")

        # X-Rate-Limit header patterns (Apple Music specific)
        if "x-rate-limit" in error_str:
            return (
                "rate_limit",
                "text",
                "Rate limit detected from X-Rate-Limit header",
            )

        # Developer token patterns (Apple Music uses developer tokens)
        if "developer token" in error_str:
            return ("permanent", "token", "Developer token issue")

        return None

    def _extract_http_status(self, exception: Exception) -> int | None:
        """Extract HTTP status code from various exception types."""
        # Check common attributes where HTTP status might be stored
        for attr in ["status_code", "http_status", "code", "response"]:
            if hasattr(exception, attr):
                value = getattr(exception, attr)
                if (
                    isinstance(value, int)
                    and HTTPStatus.HTTP_STATUS_MIN
                    <= value
                    < HTTPStatus.SERVER_ERROR_MAX
                ):
                    return value
                # Handle response objects that might contain status
                elif hasattr(value, "status_code") and not isinstance(value, int):
                    status = value.status_code
                    if (
                        isinstance(status, int)
                        and HTTPStatus.HTTP_STATUS_MIN
                        <= status
                        < HTTPStatus.SERVER_ERROR_MAX
                    ):
                        return status

        return None

    def _parse_apple_music_error_details(self, exception: Exception) -> dict[str, Any]:
        """Parse Apple Music API error details from exception.

        Apple Music API errors typically follow JSON:API error format with
        an 'errors' array containing error objects with 'code', 'detail', etc.
        """
        try:
            error_msg = str(exception)
            details: dict[str, Any] = {}

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

        except Exception:
            # If parsing fails, return empty dict
            return {}
        else:
            return details
