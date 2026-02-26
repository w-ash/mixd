"""LastFM-specific error classification for retry behavior."""

from typing import ClassVar, override

from src.infrastructure.connectors._shared.error_classification import (
    HTTPErrorClassifier,
)
from src.infrastructure.connectors.lastfm.models import LastFMAPIError


class LastFMErrorClassifier(HTTPErrorClassifier):
    """LastFM error classifier combining service-specific codes with HTTP patterns.

    LastFM uses its own error code system (1-29) rather than HTTP status codes,
    surfaced via LastFMAPIError. This classifier checks service-specific codes
    first via ``_classify_service_error()``, then falls back to the shared HTTP
    dispatch cascade in ``HTTPErrorClassifier.classify_error()``.
    """

    @property
    @override
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

    @override
    def _classify_service_error(
        self, exception: Exception
    ) -> tuple[str, str, str] | None:
        """Classify Last.fm API-level errors (HTTP 200 with error body).

        Returns ``None`` for non-LastFMAPIError exceptions so the base class
        handles httpx transport errors via the standard dispatch cascade.
        """
        if not isinstance(exception, LastFMAPIError):
            return None

        error_code = exception.status  # String "1"-"29"

        if error_code in self.PERMANENT_ERROR_CODES:
            return ("permanent", error_code, self.PERMANENT_ERROR_CODES[error_code])

        if error_code in self.TEMPORARY_ERROR_CODES:
            return ("temporary", error_code, self.TEMPORARY_ERROR_CODES[error_code])

        # Rate limiting — Last.fm error code 29
        if error_code == "29":
            return (
                "rate_limit",
                "29",
                "Rate Limit Exceded - Your IP has made too many requests in a short period, exceeding our API guidelines",
            )

        # Unknown Last.fm error code — return None to fall through to text patterns
        return None
