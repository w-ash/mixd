"""Application constants for non-configurable values.

This module contains system constants that should not be user-configurable,
such as HTTP status codes, API format specifications, and business rule limits.

For user-configurable values, see settings.py instead.
"""

from typing import Final, Literal

# Workflow progress tracking types
type Phase = Literal["fetch", "enrich", "match", "query", "save", "sync"]
type NodeType = Literal[
    "source", "enricher", "destination", "filter", "sorter", "selector"
]


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
    SEARCH_MAX_LIMIT: Final = 10  # max results per search query (Feb 2026 API)
    LIBRARY_CONTAINS_BATCH_SIZE: Final = (
        40  # /me/library/contains max per request (Feb 2026 API)
    )
    FALLBACK_SIMILARITY_THRESHOLD: Final[float] = 0.7


class BusinessLimits:
    """Business logic limits and thresholds that should not be user-configurable."""

    # Identity
    DEFAULT_USER_ID: Final = "default"

    # API pagination
    DEFAULT_PAGE_SIZE: Final = 50
    MAX_PAGE_SIZE: Final = 200
    MIN_SEARCH_LENGTH: Final = 2

    # Import processing
    MAX_UPLOAD_BYTES: Final = 100 * 1024 * 1024  # 100 MB
    DUPLICATE_RATE_EARLY_STOP: Final = 0.8

    # User-facing library limits
    DEFAULT_LIBRARY_QUERY_LIMIT: Final = 50_000  # default for liked/played source nodes
    MAX_USER_LIMIT: Final = 1_000_000  # sanity guard for limit params

    # Matching
    FULL_CONFIDENCE_SCORE: Final = 100

    # Database
    BATCH_WARNING_THRESHOLD: Final = 100
    TUPLE_IN_BATCH_SIZE: Final = 5_000  # max tuples per PostgreSQL IN clause

    # Debug
    DEBUG_LOG_TRUNCATION_LIMIT: Final = 10


class SSEConstants:
    """Server-Sent Events operational constants."""

    GRACE_PERIOD_SECONDS: Final = 30  # cleanup delay after operation completes
    MAX_CONCURRENT_OPERATIONS: Final = 3  # each holds a task, SSE queue, and DB session


class WorkflowConstants:
    """Workflow execution constants: asyncio.timeout budgets and run lifecycle.

    Per-node timeout budgets enforced via ``asyncio.timeout()`` in
    ``build_flow``'s execution loop. Source/enricher/destination nodes call
    external APIs and need generous timeouts. Transform nodes (filter, sort,
    select) are pure in-memory — 60s is a safety margin, not expected duration.
    """

    SOURCE_TIMEOUT_SECONDS: Final = 300  # 5min — external API fetches
    ENRICHER_TIMEOUT_SECONDS: Final = 300  # 5min — batch API enrichment
    TRANSFORM_TIMEOUT_SECONDS: Final = 60  # 1min — pure transforms (safety)
    DESTINATION_TIMEOUT_SECONDS: Final = 300  # 5min — external API writes

    # Run status lifecycle: PENDING → RUNNING → COMPLETED | FAILED | CANCELLED
    RUN_STATUS_PENDING: Final = "pending"
    RUN_STATUS_RUNNING: Final = "running"
    RUN_STATUS_COMPLETED: Final = "completed"
    RUN_STATUS_FAILED: Final = "failed"
    RUN_STATUS_CANCELLED: Final = "cancelled"

    # Error message limits (matches DB column String(2000))
    ERROR_MESSAGE_MAX_LENGTH: Final = 2000
    SSE_ERROR_MAX_LENGTH: Final = 500

    # Cancellation diagnostic message (DB + SSE)
    CANCELLED_BY_SERVER_MESSAGE: Final = "Cancelled by server (possible reload)"

    # Preview
    PREVIEW_OUTPUT_LIMIT: Final = 20

    # Output track metric columns (max shown in web UI tables)
    MAX_OUTPUT_METRIC_COLUMNS: Final = 5

    # SSE event type names
    SSE_EVENT_NODE_STATUS: Final = "node_status"
    SSE_EVENT_COMPLETE: Final = "complete"
    SSE_EVENT_ERROR: Final = "error"
    SSE_EVENT_PREVIEW_COMPLETE: Final = "preview_complete"


class MappingOrigin:
    """How a track mapping was established.

    Used to protect manual corrections from being overwritten by
    automated sync operations. Manual overrides are never replaced
    by subsequent ingestion or matching runs.
    """

    AUTOMATIC: Final = "automatic"
    MANUAL_OVERRIDE: Final = "manual_override"


class ReviewStatus:
    """Status of a match review item in the review queue."""

    PENDING: Final = "pending"
    ACCEPTED: Final = "accepted"
    REJECTED: Final = "rejected"


class MatchMethod:
    """Track resolution method identifiers.

    Used in connector mappings and play context to record HOW a track ID
    was resolved. Written by inward resolvers, read by play resolvers for
    context tagging, and asserted in tests.
    """

    DIRECT_IMPORT: Final = "direct_import"
    SEARCH_FALLBACK: Final = "search_fallback"
    ARTIST_TITLE: Final = "artist_title"
    SPOTIFY_REDIRECT: Final = "spotify_redirect"
    PLAY_RESOLVER: Final = "spotify_connector_play_resolver"
    LASTFM_DISCOVERY: Final = "lastfm_discovery"
    LASTFM_IMPORT: Final = "lastfm_import"
    CANONICAL_REUSE: Final = "canonical_reuse"
    ISRC_MATCH: Final = "isrc_match"
    # Secondary mappings for stale IDs (old ID → same canonical track)
    DIRECT_IMPORT_STALE_ID: Final = "direct_import_stale_id"
    SEARCH_FALLBACK_STALE_ID: Final = "search_fallback_stale_id"

    # Confidence scores for automated resolution strategies
    ISRC_MATCH_CONFIDENCE: Final = 95
    LISTENBRAINZ_REUSE_CONFIDENCE: Final = 90

    CATEGORY_ORDER: Final[tuple[str, ...]] = (
        "Primary Import",
        "Identity Resolution",
        "Cross-Service Discovery",
        "Error Recovery",
        "Secondary Cache",
    )

    CATEGORIES: Final[dict[str, str]] = {
        "direct_import": "Primary Import",
        "artist_title": "Primary Import",
        "lastfm_import": "Primary Import",
        "canonical_reuse": "Identity Resolution",
        "isrc_match": "Identity Resolution",
        "mbid_match": "Identity Resolution",
        "lastfm_discovery": "Cross-Service Discovery",
        "spotify_connector_play_resolver": "Cross-Service Discovery",
        "search_fallback": "Error Recovery",
        "spotify_redirect": "Error Recovery",
        "direct_import_stale_id": "Secondary Cache",
        "search_fallback_stale_id": "Secondary Cache",
    }

    DESCRIPTIONS: Final[dict[str, str]] = {
        "direct_import": "Standard Spotify import",
        "artist_title": "Standard Last.fm import",
        "lastfm_import": "Standard Last.fm import (with confidence)",
        "canonical_reuse": "Canonical reuse — existing track matched",
        "isrc_match": "ISRC dedup across services",
        "mbid_match": "MusicBrainz ID bridging",
        "lastfm_discovery": "Spotify found via Last.fm enrichment",
        "spotify_connector_play_resolver": "Spotify play context resolution",
        "search_fallback": "Dead Spotify ID → search fallback",
        "spotify_redirect": "Spotify ID relinking detected",
        "direct_import_stale_id": "Stale ID cache (redirect)",
        "search_fallback_stale_id": "Stale ID cache (fallback)",
    }


class DenormalizedTrackColumns:
    """Mapping from connector name to fast-path column on DBTrack."""

    COLUMN_MAP: Final[dict[str, str]] = {"spotify": "spotify_id", "musicbrainz": "mbid"}


class ConnectorPriority:
    """Connector preference order for metadata sourcing."""

    ORDER: Final[tuple[str, ...]] = ("spotify", "lastfm", "musicbrainz")


class IntegrityConstants:
    """Data integrity monitoring thresholds."""

    STALE_REVIEW_DAYS: Final = 30  # pending reviews older than this are flagged


class LastFMConstants:
    """Last.fm API format specifications and processing constants."""

    RECENT_TRACKS_PAGE_SIZE: Final = 200  # hard API limit per page
    FULL_HISTORY_LIMIT: Final = 10000  # above this, use full-history pagination


def truncate_error_message(msg: str, max_len: int) -> str:
    """Truncate an error message, appending an indicator when content is lost."""
    if len(msg) <= max_len:
        return msg
    suffix = " … [truncated]"
    return msg[: max_len - len(suffix)] + suffix
