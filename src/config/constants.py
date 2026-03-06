"""Application constants for non-configurable values.

This module contains system constants that should not be user-configurable,
such as HTTP status codes, API format specifications, and business rule limits.

For user-configurable values, see settings.py instead.
"""

from typing import Final


class HTTPStatus:
    """HTTP status range boundaries for connector error classification.

    Uses range constants (not individual codes) because ErrorClassifier
    categorizes responses by range (4xx = client, 5xx = server) rather
    than matching specific status codes. stdlib http.HTTPStatus provides
    individual codes but not the range boundaries needed here.
    """

    INTERNAL_SERVER_ERROR: Final = 500
    SERVER_ERROR_MAX: Final = 600
    HTTP_STATUS_MIN: Final = 100
    CLIENT_ERROR_MIN: Final = 400
    CLIENT_ERROR_MAX: Final = 500


class SpotifyConstants:
    """Spotify API format specifications and validation constants."""

    URI_PARTS_COUNT: Final = 3  # "spotify:track:<id>"
    TRACK_ID_LENGTH: Final = 22
    TRACKS_BULK_LIMIT: Final = 50  # max items per /tracks bulk-fetch


class BusinessLimits:
    """Business logic limits and thresholds that should not be user-configurable."""

    # Identity
    DEFAULT_USER_ID: Final = "default"

    # API pagination
    DEFAULT_PAGE_SIZE: Final = 50
    MAX_PAGE_SIZE: Final = 200
    MAX_USER_LIMIT: Final = 10000
    MIN_SEARCH_LENGTH: Final = 2

    # Import processing
    MAX_UPLOAD_BYTES: Final = 100 * 1024 * 1024  # 100 MB
    DUPLICATE_RATE_EARLY_STOP: Final = 0.8

    # Matching
    FULL_CONFIDENCE_SCORE: Final = 100

    # Database
    SQLITE_BATCH_WARNING_THRESHOLD: Final = 100

    # Debug
    DEBUG_LOG_TRUNCATION_LIMIT: Final = 10


class SSEConstants:
    """Server-Sent Events operational constants."""

    GRACE_PERIOD_SECONDS: Final = 30  # cleanup delay after operation completes
    MAX_CONCURRENT_OPERATIONS: Final = 3  # each holds a task, SSE queue, and DB session


class ConnectorConstants:
    """Connector identification constants."""

    # Pseudo-connector name for internal DB track IDs (filtered from API responses)
    DB_PSEUDO_CONNECTOR: Final = "db"


class LastFMConstants:
    """Last.fm API format specifications and processing constants."""

    RECENT_TRACKS_PAGE_SIZE: Final = 200  # hard API limit per page
    FULL_HISTORY_LIMIT: Final = 10000  # above this, use full-history pagination
