"""Data structures for music service operations and synchronization tracking.

Contains classes for recording play events, sync progress, and operation results
from music services like Spotify and Last.fm.
"""

# pyright: reportExplicitAny=false, reportAny=false
# Legitimate Any: service_metadata, raw_data dicts, factory patterns

from datetime import datetime
from typing import TYPE_CHECKING, Any, Final, Self

from attrs import Attribute, define, field

if TYPE_CHECKING:
    from src.infrastructure.connectors.spotify.personal_data import SpotifyPlayRecord

from .shared import MetricValue
from .summary_metrics import SummaryMetricCollection
from .track import Track, TrackList


class Unset:
    """Sentinel type distinguishing 'not provided' from None.

    Used in SyncCheckpoint.with_update() so that:
    - Omitting cursor → preserves existing value
    - Passing None → explicitly clears cursor
    """

    __slots__ = ()


UNSET: Final = Unset()


@define(frozen=True, slots=True)
class SyncCheckpoint:
    """Tracks sync progress for resuming interrupted music service synchronization.

    Records the last processed timestamp and pagination cursor so sync operations
    can resume where they left off if interrupted.
    """

    user_id: str
    service: str
    entity_type: str  # 'likes', 'plays'
    last_timestamp: datetime | None = None
    cursor: str | None = None  # For pagination/continuation
    id: int | None = None

    def with_update(
        self,
        timestamp: datetime,
        cursor: str | Unset | None = UNSET,
    ) -> Self:
        """Returns new checkpoint with updated timestamp and optional cursor.

        Args:
            timestamp: Latest sync timestamp to record
            cursor: Pagination cursor — omit to preserve, pass None to clear

        Returns:
            New checkpoint instance with updated values
        """
        return self.__class__(
            user_id=self.user_id,
            service=self.service,
            entity_type=self.entity_type,
            last_timestamp=timestamp,
            cursor=self.cursor if isinstance(cursor, Unset) else cursor,
            id=self.id,
        )


@define(frozen=True, slots=True)
class SyncCheckpointStatus:
    """Status information about a sync checkpoint for UI display.

    Provides checkpoint information that interfaces need to show users
    the current state of sync operations and help them make informed decisions.
    """

    service: str
    entity_type: str
    last_sync_timestamp: datetime | None = None
    has_previous_sync: bool = False

    def format_timestamp(self) -> str | None:
        """Format timestamp for display."""
        if self.last_sync_timestamp:
            return self.last_sync_timestamp.strftime("%Y-%m-%d %H:%M:%S")
        return None


# Standard field names for track metadata across music services
class TrackContextFields:
    """Standard field names for track metadata across music services.

    Provides consistent naming for track data from different APIs (Spotify, Last.fm)
    to avoid field name conflicts and enable unified processing.
    """

    # Core track metadata (used by all services)
    TRACK_NAME: Final[str] = "track_name"
    ARTIST_NAME: Final[str] = "artist_name"
    ALBUM_NAME: Final[str] = "album_name"

    # Service-specific identifiers
    SPOTIFY_TRACK_URI: Final[str] = "spotify_track_uri"
    LASTFM_TRACK_URL: Final[str] = "lastfm_track_url"
    LASTFM_ARTIST_URL: Final[str] = "lastfm_artist_url"
    LASTFM_ALBUM_URL: Final[str] = "lastfm_album_url"

    # Behavioral metadata
    PLATFORM: Final[str] = "platform"
    COUNTRY: Final[str] = "country"
    REASON_START: Final[str] = "reason_start"
    REASON_END: Final[str] = "reason_end"
    SHUFFLE: Final[str] = "shuffle"
    SKIPPED: Final[str] = "skipped"
    OFFLINE: Final[str] = "offline"
    INCOGNITO_MODE: Final[str] = "incognito_mode"


@define(frozen=True, slots=True)
class PlayRecord:
    """Raw play data from a music service before normalization.

    Stores track play information as received from service APIs with
    service-specific metadata preserved for later processing.

    Args:
        artist_name: Track artist name
        track_name: Track title
        played_at: When the track was played/scrobbled
        service: Source service ("spotify", "lastfm", etc.)
        album_name: Album name if available
        ms_played: Play duration in milliseconds (Spotify only)
        service_metadata: Service-specific data as key-value pairs
        api_page: Source API page number for debugging
        raw_data: Complete API response for debugging
    """

    # Core fields (mandatory)
    artist_name: str
    track_name: str
    played_at: datetime  # When track was played/scrobbled
    service: str  # "spotify", "lastfm", etc.

    # Optional core fields
    album_name: str | None = None
    ms_played: int | None = None  # Spotify has this, Last.fm doesn't

    # Service-specific metadata stored as dict for flexibility
    service_metadata: dict[str, Any] = field(factory=dict)

    # Import tracking
    api_page: int | None = None
    raw_data: dict[str, Any] = field(factory=dict)


def _validate_timezone_aware_datetime(
    _instance: object, attribute: Attribute[Any], value: datetime | None
) -> None:
    """Validator to ensure datetime fields are timezone-aware."""
    if value is not None and value.tzinfo is None:
        raise ValueError(
            f"Field '{attribute.name}' must be timezone-aware. Use datetime.now(UTC) or datetime.replace(tzinfo=UTC) for naive datetimes."
        )


@define(frozen=True, slots=True)
class ConnectorTrackPlay:
    """Raw play data from external music services before resolution to canonical tracks.

    Unified entity for both Spotify and Last.fm play imports using deferred resolution pattern.
    Stores complete raw API data with resolution tracking for eventual conversion to TrackPlay.

    This replaces the PlayRecord entity and implements the connector pattern for plays,
    following the same architecture as ConnectorTrack for consistent separation of
    ingestion and resolution concerns.
    """

    # Raw API data (core fields from all services)
    artist_name: str
    track_name: str
    played_at: datetime = field(validator=_validate_timezone_aware_datetime)
    service: str  # "spotify", "lastfm"
    user_id: str = "default"
    album_name: str | None = None
    ms_played: int | None = None
    service_metadata: dict[str, Any] = field(factory=dict)

    # Import tracking (for debugging and batch management)
    api_page: int | None = None
    raw_data: dict[str, Any] = field(factory=dict)
    import_timestamp: datetime | None = field(
        default=None, validator=_validate_timezone_aware_datetime
    )
    import_source: str | None = None  # "lastfm_api", "spotify_export"
    import_batch_id: str | None = None

    # Connector identification (auto-derived in __attrs_post_init__)
    connector_name: str = field(init=False)
    connector_track_identifier: str = field(init=False)

    # Resolution tracking (nullable until resolved)
    resolved_track_id: int | None = None
    resolved_at: datetime | None = field(
        default=None, validator=_validate_timezone_aware_datetime
    )

    # Database persistence
    id: int | None = None

    def __attrs_post_init__(self) -> None:
        """Auto-derive connector fields based on service type."""
        # Set connector_name from service
        object.__setattr__(self, "connector_name", self.service)

        # Create service-specific track identifier
        if self.service == "spotify":
            # Use Spotify track URI if available, fallback to artist::title
            track_uri = self.service_metadata.get("track_uri")
            identifier = track_uri or f"{self.artist_name}::{self.track_name}"
        elif self.service == "lastfm":
            # Last.fm uses artist::title pattern (no stable IDs)
            identifier = f"{self.artist_name}::{self.track_name}"
        else:
            # Generic fallback for other services
            identifier = f"{self.artist_name}::{self.track_name}"

        object.__setattr__(self, "connector_track_identifier", identifier)

    @classmethod
    def create_from_spotify_record(
        cls,
        spotify_record: SpotifyPlayRecord,
        import_timestamp: datetime | None = None,
        import_batch_id: str | None = None,
    ) -> Self:
        """Create ConnectorTrackPlay from SpotifyPlayRecord for unified processing.

        Args:
            spotify_record: Parsed Spotify personal data record
            import_timestamp: When this import was initiated
            import_batch_id: Batch identifier for bulk imports

        Returns:
            ConnectorTrackPlay object ready for deferred resolution
        """
        return cls(
            artist_name=spotify_record.artist_name,
            track_name=spotify_record.track_name,
            played_at=spotify_record.timestamp,
            service="spotify",
            album_name=spotify_record.album_name,
            ms_played=spotify_record.ms_played,
            service_metadata={
                "track_uri": spotify_record.track_uri,
                "platform": spotify_record.platform,
                "country": spotify_record.country,
                "reason_start": spotify_record.reason_start,
                "reason_end": spotify_record.reason_end,
                "shuffle": spotify_record.shuffle,
                "skipped": spotify_record.skipped,
                "offline": spotify_record.offline,
                "incognito_mode": spotify_record.incognito_mode,
            },
            import_timestamp=import_timestamp,
            import_source="spotify_export",
            import_batch_id=import_batch_id,
            raw_data={
                "original_spotify_record": {
                    "timestamp": spotify_record.timestamp.isoformat(),
                    "track_uri": spotify_record.track_uri,
                    "track_name": spotify_record.track_name,
                    "artist_name": spotify_record.artist_name,
                    "album_name": spotify_record.album_name,
                    "ms_played": spotify_record.ms_played,
                }
            },
        )


@define(frozen=True, slots=True)
class TrackPlay:
    """Normalized record of when a user played a track on any music service.

    Contains standardized play event data with service context preserved
    for deduplication and analytics.

    Args:
        track_id: Database ID of the track
        service: Source service ("spotify", "lastfm", etc.)
        played_at: When the track was played (must be timezone-aware)
        ms_played: Duration played in milliseconds
        context: Service metadata and behavioral data
        id: Database ID of this play record
        import_timestamp: When this play was imported (must be timezone-aware if provided)
        import_source: How this play was imported (API, export file, etc.)
        import_batch_id: Batch identifier for bulk imports
    """

    track_id: int | None
    service: str
    played_at: datetime = field(validator=_validate_timezone_aware_datetime)
    user_id: str = "default"
    ms_played: int | None = None
    context: dict[str, Any] | None = None
    id: int | None = None

    # Cross-source deduplication: which services contributed to this play record
    source_services: list[str] | None = None

    # Import tracking (service-agnostic)
    import_timestamp: datetime | None = field(
        default=None, validator=_validate_timezone_aware_datetime
    )
    import_source: str | None = None  # "spotify_export", "lastfm_api", "manual"
    import_batch_id: str | None = None


@define(frozen=False)
class OperationResult:
    """Collects results and metrics from music data operations.

    Uses self-describing summary metrics to eliminate hardcoded UI labels.

    Attributes:
        operation_name: Human-readable operation identifier
        summary_metrics: Self-describing operation-level metrics (counts, rates, aggregates)
        tracks: List of processed Track objects
        execution_time: Operation duration in seconds
        metadata: Operation metadata (batch_id, checkpoint, etc.)
        metrics: Per-track operational metrics (track_id -> metric_value mappings)
        tracklist: Optional TrackList with metadata
    """

    operation_name: str = field(default="")
    summary_metrics: SummaryMetricCollection = field(factory=SummaryMetricCollection)
    tracks: list[Track] = field(factory=list)
    execution_time: float = field(default=0.0)
    metadata: dict[str, Any] = field(factory=dict)
    metrics: dict[str, dict[int, MetricValue]] = field(
        factory=dict,
    )  # Per-track operational metrics: metric_name -> {track_id -> value}
    tracklist: TrackList | None = field(
        default=None
    )  # Optional tracklist with metadata

    def get_metric(
        self,
        track_id: int | None,
        metric_name: str,
        default: MetricValue = None,
    ) -> MetricValue:
        """Get specific per-track metric value.

        Args:
            track_id: The ID of the track to get the metric for
            metric_name: Name of the metric to retrieve
            default: Value to return if metric is not found

        Returns:
            The metric value for the track, or default if not found
        """
        if track_id is None:
            return default
        return self.metrics.get(metric_name, {}).get(track_id, default)

    def to_dict(self) -> dict[str, Any]:
        """Converts result to JSON-serializable dictionary for API responses.

        Returns:
            Dictionary with operation stats, summary metrics, track details, and per-track metrics
        """
        result: dict[str, Any] = {
            "operation_name": self.operation_name,
            "execution_time": self.execution_time,
            "track_count": len(self.tracks),
        }

        # Add summary metrics
        if self.summary_metrics.metrics:
            result["summary_metrics"] = [
                {
                    "name": m.name,
                    "value": m.value,
                    "label": m.label,
                    "format": m.format,
                }
                for m in self.summary_metrics.sorted()
            ]

        # Add metadata if present
        if self.metadata:
            result["metadata"] = self.metadata.copy()

        # Add track details
        if self.tracks:
            result["tracks"] = [
                {
                    "id": t.id,
                    "title": t.title,
                    "artists": [a.name for a in t.artists],
                    "metrics": {
                        name: values.get(t.id)
                        for name, values in self.metrics.items()
                        if t.id and t.id in values
                    },
                }
                for t in self.tracks
            ]

        # Add per-track metrics summary if present
        if self.metrics:
            result["per_track_metrics_summary"] = {
                name: {
                    "total_tracks": len(values),
                    "avg_value": (
                        sum(v for v in values.values() if isinstance(v, (int, float)))
                        / len([
                            v for v in values.values() if isinstance(v, (int, float))
                        ])
                    )
                    if any(isinstance(v, (int, float)) for v in values.values())
                    else None,
                }
                for name, values in self.metrics.items()
            }

        return result


# Factory function for creating Last.fm play records with proper metadata
def create_lastfm_play_record(
    artist_name: str,
    track_name: str,
    scrobbled_at: datetime,
    album_name: str | None = None,
    lastfm_track_url: str | None = None,
    lastfm_artist_url: str | None = None,
    lastfm_album_url: str | None = None,
    mbid: str | None = None,
    artist_mbid: str | None = None,
    album_mbid: str | None = None,
    streamable: bool = False,
    loved: bool = False,
    api_page: int | None = None,
    raw_data: dict[str, Any] | None = None,
) -> PlayRecord:
    """Creates Last.fm PlayRecord with service-specific metadata properly formatted.

    Args:
        artist_name: Track artist name
        track_name: Track title
        scrobbled_at: When track was scrobbled to Last.fm
        album_name: Album name if available
        lastfm_track_url: Last.fm track page URL
        lastfm_artist_url: Last.fm artist page URL
        lastfm_album_url: Last.fm album page URL
        mbid: MusicBrainz track ID
        artist_mbid: MusicBrainz artist ID
        album_mbid: MusicBrainz album ID
        streamable: Whether track is streamable on Last.fm
        loved: Whether user has loved this track
        api_page: Source API page number
        raw_data: Complete API response for debugging

    Returns:
        PlayRecord with Last.fm metadata in standardized format
    """
    # Build Last.fm specific metadata using standardized field names
    service_metadata = {
        TrackContextFields.LASTFM_TRACK_URL: lastfm_track_url,
        TrackContextFields.LASTFM_ARTIST_URL: lastfm_artist_url,
        TrackContextFields.LASTFM_ALBUM_URL: lastfm_album_url,
        "mbid": mbid,
        "artist_mbid": artist_mbid,
        "album_mbid": album_mbid,
        "streamable": streamable,
        "loved": loved,
    }

    return PlayRecord(
        artist_name=artist_name,
        track_name=track_name,
        played_at=scrobbled_at,
        service="lastfm",
        album_name=album_name,
        ms_played=None,  # Last.fm doesn't provide duration
        service_metadata=service_metadata,
        api_page=api_page,
        raw_data=raw_data or {},
    )
