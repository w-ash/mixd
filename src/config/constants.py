"""Application constants for non-configurable values.

This module contains system constants that should not be user-configurable,
such as HTTP status codes, API format specifications, and business rule limits.

For user-configurable values, see settings.py instead.
"""


class HTTPStatus:
    """Standard HTTP status codes used across connector error handling."""
    
    # Client error responses (4xx)
    BAD_REQUEST = 400
    UNAUTHORIZED = 401
    FORBIDDEN = 403
    NOT_FOUND = 404
    TOO_MANY_REQUESTS = 429
    
    # Server error responses (5xx)
    INTERNAL_SERVER_ERROR = 500
    BAD_GATEWAY = 502
    SERVICE_UNAVAILABLE = 503
    GATEWAY_TIMEOUT = 504
    SERVER_ERROR_MAX = 600
    
    # HTTP status ranges
    HTTP_STATUS_MIN = 100
    CLIENT_ERROR_MIN = 400
    CLIENT_ERROR_MAX = 500


class SpotifyConstants:
    """Spotify API format specifications and validation constants."""
    
    # Spotify URI format: "spotify:track:3tI6o5tSlbB2trBl5UKJ1z"
    URI_PARTS_COUNT = 3
    TRACK_ID_LENGTH = 22
    
    # Spotify API limits
    TRACKS_BULK_LIMIT = 50


class BusinessLimits:
    """Business logic limits and thresholds that should not be user-configurable."""
    
    # User-facing API limits
    MAX_USER_LIMIT = 10000
    
    # Confidence scoring
    FULL_CONFIDENCE_SCORE = 100
    
    # File processing limits  
    LARGE_FILE_WARNING_MB = 100
    
    # Database processing thresholds
    SQLITE_BATCH_WARNING_THRESHOLD = 100
    MEMORY_WARNING_THRESHOLD = 10000


class LastFMConstants:
    """Last.fm API format specifications and processing constants."""
    
    # Last.fm API limits and thresholds
    RECENT_TRACKS_MAX_LIMIT = 200
    FULL_HISTORY_LIMIT = 10000


class MusicBrainzConstants:
    """MusicBrainz data format specifications."""
    
    # ISRC format validation
    ISRC_LENGTH = 12