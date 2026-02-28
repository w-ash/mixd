"""Application constants for non-configurable values.

This module contains system constants that should not be user-configurable,
such as HTTP status codes, API format specifications, and business rule limits.

For user-configurable values, see settings.py instead.
"""

from typing import Final


class HTTPStatus:
    """Standard HTTP status codes used across connector error handling."""

    # Server error responses (5xx)
    INTERNAL_SERVER_ERROR: Final[int] = 500
    SERVER_ERROR_MAX: Final[int] = 600

    # HTTP status ranges
    HTTP_STATUS_MIN: Final[int] = 100
    CLIENT_ERROR_MIN: Final[int] = 400
    CLIENT_ERROR_MAX: Final[int] = 500


class SpotifyConstants:
    """Spotify API format specifications and validation constants."""

    # Spotify URI format: "spotify:track:3tI6o5tSlbB2trBl5UKJ1z"
    URI_PARTS_COUNT: Final[int] = 3
    TRACK_ID_LENGTH: Final[int] = 22

    # Spotify API limits
    TRACKS_BULK_LIMIT: Final[int] = 50


class BusinessLimits:
    """Business logic limits and thresholds that should not be user-configurable."""

    # User-facing API limits
    MAX_USER_LIMIT: Final[int] = 10000

    # Confidence scoring
    FULL_CONFIDENCE_SCORE: Final[int] = 100

    # Database processing thresholds
    SQLITE_BATCH_WARNING_THRESHOLD: Final[int] = 100


class LastFMConstants:
    """Last.fm API format specifications and processing constants."""

    # Last.fm API limits and thresholds
    RECENT_TRACKS_PAGE_SIZE: Final[int] = 200  # Hard API limit per page
    FULL_HISTORY_LIMIT: Final[int] = 10000
