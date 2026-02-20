"""MusicBrainz-specific error classification for retry behavior.

MusicBrainz API primarily uses HTTP status codes for error signaling,
with special handling for rate limiting (503 Service Unavailable).

Key patterns:
- 503: Rate limiting (max 1 req/sec)
- 404: Not found
- 400-403: Client errors (permanent)
- 500-504: Server errors (temporary)
"""

from typing import override

from src.infrastructure.connectors._shared.error_classification import (
    HTTPErrorClassifier,
)


class MusicBrainzErrorClassifier(HTTPErrorClassifier):
    """MusicBrainz error classifier using HTTP patterns.

    MusicBrainz API uses standard HTTP status codes with special emphasis
    on 503 for rate limiting (enforces max 1 request per second). This
    classifier leverages the base HTTPErrorClassifier while adding
    MusicBrainz-specific context.

    Error categories:
        - rate_limit: 503 Service Unavailable (rate limit enforcement)
        - not_found: 404 Not Found (resource doesn't exist)
        - permanent: 400-403 (client errors)
        - temporary: 500-504 (server errors)

    Example:
        >>> classifier = MusicBrainzErrorClassifier()
        >>> error_type, code, desc = classifier.classify_error(exception)
        >>> if error_type == "rate_limit":
        ...     # Back off and retry
        ...     pass
    """

    @property
    @override
    def service_name(self) -> str:
        """Return service name for logging."""
        return "musicbrainz"

    def classify_error(self, exception: Exception) -> tuple[str, str, str]:
        """Classify MusicBrainz API errors.

        MusicBrainz uses standard HTTP status codes:
        - 503: Rate limiting (max 1 req/sec) - RETRY with backoff
        - 404: Not found - FAIL FAST
        - 400-403: Client errors - FAIL FAST
        - 500-504: Server errors - RETRY

        Args:
            exception: Exception from MusicBrainz API call

        Returns:
            Tuple of (error_type, error_code, error_description)
            - error_type: "permanent", "temporary", "rate_limit", "not_found", "unknown"
            - error_code: HTTP status code or "text" for pattern matches
            - error_description: Human-readable description
        """
        error_str = str(exception).lower()

        # MusicBrainz rate limiting uses 503 Service Unavailable
        # This is their primary rate limit enforcement mechanism
        if "503" in error_str or "rate limit" in error_str:
            return ("rate_limit", "503", "Rate limit exceeded (1 req/sec)")

        # Try base class text pattern classification
        # This handles common HTTP error patterns
        if result := self.classify_text_patterns(error_str):
            return result

        # Default to unknown - will be retried defensively
        return ("unknown", "N/A", str(exception))
