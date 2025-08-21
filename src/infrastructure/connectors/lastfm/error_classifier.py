"""LastFM-specific error classification for retry behavior."""

import pylast

from src.config.constants import HTTPStatus
from src.infrastructure.connectors._shared.error_classification import (
    BaseErrorClassifier,
)


class LastFMErrorClassifier(BaseErrorClassifier):
    """LastFM-specific error classifier with comprehensive API error handling."""

    def classify_error(self, exception: Exception) -> tuple[str, str, str]:
        """Classify Last.fm API errors for proper retry behavior.

        Args:
            exception: The exception to classify

        Returns:
            Tuple of (error_type, error_code, error_description)
            error_type: "permanent", "temporary", "rate_limit", "not_found", "unknown"
        """
        if not isinstance(exception, pylast.WSError):
            return ("unknown", "N/A", str(exception))

        error_str = str(exception).lower()
        error_code = exception.status  # Direct access to error code

        # Official Last.fm error codes - Permanent errors (don't retry)
        permanent_patterns = {
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
        temporary_patterns = {
            "8": "Operation failed - Most likely the backend service failed. Please try again",
            "9": "Invalid session key - Please re-authenticate",
            "11": "Service Offline - This service is temporarily offline. Try again later",
            "16": "The service is temporarily unavailable, please try again",
            "20": "Not Enough Content - There is not enough content to play this station",
        }

        # Rate limiting (retry with constant delay)
        if error_code == "29" or "rate limit" in error_str:
            return (
                "rate_limit",
                "29",
                "Rate Limit Exceded - Your IP has made too many requests in a short period, exceeding our API guidelines",
            )

        # Check for specific error codes
        if error_code in permanent_patterns:
            return ("permanent", error_code, permanent_patterns[error_code])

        if error_code in temporary_patterns:
            return ("temporary", error_code, temporary_patterns[error_code])

        # Check for textual patterns when error codes aren't present
        if any(
            pattern in error_str
            for pattern in ["rate limit", "too many", "quota", "throttle"]
        ):
            return ("rate_limit", "text", "Rate limit detected from response text")

        # Track/resource not found (context dependent - not necessarily permanent)
        if (
            "not found" in error_str
            or "does not exist" in error_str
            or "no such" in error_str
        ):
            return ("not_found", "N/A", "Resource not found")

        if any(
            pattern in error_str
            for pattern in [
                "timeout",
                "connection",
                "network",
                "server error",
                "503",
                "502",
                "500",
            ]
        ):
            return ("temporary", "text", "Network or server error from response text")

        if any(
            pattern in error_str
            for pattern in [
                "unauthorized",
                "forbidden",
                "invalid key",
                "authentication",
            ]
        ):
            return (
                "permanent",
                "text",
                "Authentication/authorization error from response text",
            )

        # Default to unknown for unrecognized errors
        return ("unknown", "N/A", str(exception))
