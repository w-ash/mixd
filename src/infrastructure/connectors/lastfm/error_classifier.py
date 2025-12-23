"""LastFM-specific error classification for retry behavior."""

from __future__ import annotations

from typing import ClassVar

import pylast

from src.infrastructure.connectors._shared.error_classification import (
    HTTPErrorClassifier,
)


class LastFMErrorClassifier(HTTPErrorClassifier):
    """LastFM error classifier combining service-specific codes with HTTP patterns.

    LastFM uses its own error code system (1-29) rather than HTTP status codes.
    This classifier checks service-specific codes first, then falls back to
    HTTPErrorClassifier's text pattern matching for generic errors.
    """

    @property
    def service_name(self) -> str:
        """Return service name for logging."""
        return "lastfm"

    # Official Last.fm error codes - Permanent errors (don't retry)
    PERMANENT_ERROR_CODES: ClassVar[dict[str, str]] = {
        "2": "Invalid service - This service does not exist",
        "3": "Invalid Method - No method with that name in this package",
        "4": "Authentication Failed - You do not have permissions to access the service",
        "5": "Invalid format - This service doesn't exist in that format",
        "6": "Invalid parameters - Your request is missing a required parameter",
        "7": "Invalid resource specified",
        "10": "Invalid API key - You must be granted a valid key by last.fm",
        "12": "Subscribers Only - This station is only available to paid last.fm subscribers",
        "13": "Invalid method signature supplied",
        "14": "Unauthorized Token - This token has not been authorized",
        "15": "This item is not available for streaming",
        "17": "Login: User requires to be logged in",
        "18": "Trial Expired - This user has no free radio plays left. Subscription required",
        "21": "Not Enough Members - This group does not have enough members for radio",
        "22": "Not Enough Fans - This artist does not have enough fans for for radio",
        "23": "Not Enough Neighbours - There are not enough neighbours for radio",
        "24": "No Peak Radio - This user is not allowed to listen to radio during peak usage",
        "25": "Radio Not Found - Radio station not found",
        "26": "API Key Suspended - This application is not allowed to make requests to the web services",
        "27": "Deprecated - This type of request is no longer supported",
    }

    # Temporary errors (retry with exponential backoff)
    TEMPORARY_ERROR_CODES: ClassVar[dict[str, str]] = {
        "8": "Operation failed - Most likely the backend service failed. Please try again",
        "9": "Invalid session key - Please re-authenticate",
        "11": "Service Offline - This service is temporarily offline. Try again later",
        "16": "The service is temporarily unavailable, please try again",
        "20": "Not Enough Content - There is not enough content to play this station",
    }

    def classify_error(self, exception: Exception) -> tuple[str, str, str]:
        """Classify Last.fm API errors for proper retry behavior.

        LastFM uses service-specific error codes (1-29). This classifier checks
        those codes first, then delegates to parent HTTPErrorClassifier for
        generic text pattern matching (rate limits, network errors, etc.).

        Args:
            exception: The exception to classify

        Returns:
            Tuple of (error_type, error_code, error_description)
            error_type: "permanent", "temporary", "rate_limit", "not_found", "unknown"
        """
        # Handle non-LastFM exceptions
        if not isinstance(exception, pylast.WSError):
            # Delegate to base class text pattern classification
            error_str = str(exception).lower()
            if result := self.classify_text_patterns(error_str):
                return result
            return ("unknown", "N/A", str(exception))

        # LastFM-specific error code classification (highest priority)
        error_code = exception.status  # LastFM service error code
        error_str = str(exception).lower()

        # Check service-specific error codes FIRST
        if error_code in self.PERMANENT_ERROR_CODES:
            return ("permanent", error_code, self.PERMANENT_ERROR_CODES[error_code])

        if error_code in self.TEMPORARY_ERROR_CODES:
            return ("temporary", error_code, self.TEMPORARY_ERROR_CODES[error_code])

        # Rate limiting - LastFM error code 29
        if error_code == "29":
            return (
                "rate_limit",
                "29",
                "Rate Limit Exceded - Your IP has made too many requests in a short period, exceeding our API guidelines",
            )

        # Fall back to base class text pattern classification
        # This handles: rate limits, network errors, auth errors, not found, etc.
        if result := self.classify_text_patterns(error_str):
            return result

        # Default to unknown for unrecognized errors
        return ("unknown", "N/A", str(exception))
