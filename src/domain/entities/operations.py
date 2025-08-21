"""Data structures for music service operations and synchronization tracking.

Contains classes for recording play events, sync progress, and operation results
from music services like Spotify and Last.fm.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from attrs import define, field

if TYPE_CHECKING:
    from src.infrastructure.connectors.spotify.personal_data import SpotifyPlayRecord

from .track import Artist, Track, TrackList


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
        cursor: str | None = None,
    ) -> "SyncCheckpoint":
        """Returns new checkpoint with updated timestamp and optional cursor.

        Args:
            timestamp: Latest sync timestamp to record
            cursor: Pagination cursor for next API call (optional)

        Returns:
            New checkpoint instance with updated values
        """
        return self.__class__(
            user_id=self.user_id,
            service=self.service,
            entity_type=self.entity_type,
            last_timestamp=timestamp,
            cursor=cursor or self.cursor,
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
    TRACK_NAME = "track_name"
    ARTIST_NAME = "artist_name"
    ALBUM_NAME = "album_name"

    # Service-specific identifiers
    SPOTIFY_TRACK_URI = "spotify_track_uri"
    LASTFM_TRACK_URL = "lastfm_track_url"
    LASTFM_ARTIST_URL = "lastfm_artist_url"
    LASTFM_ALBUM_URL = "lastfm_album_url"

    # Behavioral metadata
    PLATFORM = "platform"
    COUNTRY = "country"
    REASON_START = "reason_start"
    REASON_END = "reason_end"
    SHUFFLE = "shuffle"
    SKIPPED = "skipped"
    OFFLINE = "offline"
    INCOGNITO_MODE = "incognito_mode"


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


def _validate_timezone_aware_datetime(_instance, attribute, value):
    """Validator to ensure datetime fields are timezone-aware."""
    if value is not None and value.tzinfo is None:
        raise ValueError(
            f"Field '{attribute.name}' must be timezone-aware. "
            f"Use datetime.now(UTC) or datetime.replace(tzinfo=UTC) for naive datetimes."
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
        spotify_record: "SpotifyPlayRecord",
        import_timestamp: datetime | None = None,
        import_batch_id: str | None = None,
    ) -> "ConnectorTrackPlay":
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

    @classmethod
    def create_from_lastfm_data(
        cls,
        artist_name: str,
        track_name: str,
        played_at: datetime,
        album_name: str | None = None,
        ms_played: int | None = None,
        service_metadata: dict[str, Any] | None = None,
        api_page: int | None = None,
        raw_data: dict[str, Any] | None = None,
        import_timestamp: datetime | None = None,
        import_batch_id: str | None = None,
    ) -> "ConnectorTrackPlay":
        """Create ConnectorTrackPlay from Last.fm API data for unified processing.

        Args:
            artist_name: Track artist name
            track_name: Track title
            played_at: When track was scrobbled
            album_name: Album name if available
            ms_played: Play duration (Last.fm doesn't provide this)
            service_metadata: Last.fm specific metadata
            api_page: Source API page for debugging
            raw_data: Complete API response for debugging
            import_timestamp: When this import was initiated
            import_batch_id: Batch identifier for bulk imports

        Returns:
            ConnectorTrackPlay object ready for deferred resolution
        """
        return cls(
            artist_name=artist_name,
            track_name=track_name,
            played_at=played_at,
            service="lastfm",
            album_name=album_name,
            ms_played=ms_played,
            service_metadata=service_metadata or {},
            api_page=api_page,
            raw_data=raw_data or {},
            import_timestamp=import_timestamp,
            import_source="lastfm_api",
            import_batch_id=import_batch_id,
        )

    def is_resolved(self) -> bool:
        """Check if this connector play has been resolved to a canonical track."""
        return self.resolved_track_id is not None

    def with_resolution(
        self, track_id: int, resolved_at: datetime | None = None
    ) -> "ConnectorTrackPlay":
        """Create new ConnectorTrackPlay with resolution information.

        Args:
            track_id: Canonical track ID this play resolves to
            resolved_at: When resolution occurred (defaults to now)

        Returns:
            New ConnectorTrackPlay instance with resolution data
        """
        import attrs

        return attrs.evolve(
            self,
            resolved_track_id=track_id,
            resolved_at=resolved_at or datetime.now(UTC),
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
    ms_played: int | None = None
    context: dict[str, Any] | None = None
    id: int | None = None

    # Import tracking (service-agnostic)
    import_timestamp: datetime | None = field(
        default=None, validator=_validate_timezone_aware_datetime
    )
    import_source: str | None = None  # "spotify_export", "lastfm_api", "manual"
    import_batch_id: str | None = None

    @classmethod
    def create_with_current_import_timestamp(
        cls, track_id: int | None, service: str, played_at: datetime, **kwargs
    ) -> "TrackPlay":
        """Create TrackPlay with current UTC timestamp for import tracking."""
        return cls(
            track_id=track_id,
            service=service,
            played_at=played_at,
            import_timestamp=datetime.now(UTC),
            **kwargs,
        )

    def to_track_metadata(self) -> dict[str, Any]:
        """Extracts track identifying metadata for duplicate detection.

        Returns:
            Dictionary with title, artist, album and service URLs for matching
        """
        if not self.context:
            return {}

        return {
            "title": self.context.get(TrackContextFields.TRACK_NAME, ""),
            "artist": self.context.get(TrackContextFields.ARTIST_NAME, ""),
            "album": self.context.get(TrackContextFields.ALBUM_NAME),
            "duration_ms": self.ms_played,
            # Additional metadata for service-specific matching
            TrackContextFields.SPOTIFY_TRACK_URI: self.context.get(
                TrackContextFields.SPOTIFY_TRACK_URI
            ),
            TrackContextFields.LASTFM_TRACK_URL: self.context.get(
                TrackContextFields.LASTFM_TRACK_URL
            ),
        }

    def to_track(self) -> Track:
        """Creates Track object from play data for similarity scoring.

        Returns:
            Track instance with artist, title, and duration for comparison
        """
        if not self.context:
            # Fallback for plays without context
            return Track(title="Unknown", artists=[Artist(name="Unknown")])

        artist_name = self.context.get(TrackContextFields.ARTIST_NAME, "Unknown")
        track_title = self.context.get(TrackContextFields.TRACK_NAME, "Unknown")
        album_name = self.context.get(TrackContextFields.ALBUM_NAME)

        return Track(
            title=track_title,
            artists=[Artist(name=artist_name)],
            album=album_name,
            duration_ms=self.ms_played,
            id=self.track_id,
        )


@define(frozen=False)
class OperationResult:
    """Collects results and metrics from music data operations.

    Tracks processed tracks, timing, success/failure counts, and per-track metrics
    for operations like syncing likes, importing plays, or running workflows.
    Provides statistics calculation and JSON serialization for API responses.

    Attributes:
        tracks: List of processed Track objects
        metrics: Per-track metrics (track_id -> metric_value mappings)
        operation_name: Human-readable operation identifier
        execution_time: Operation duration in seconds
        tracklist: Optional TrackList with metadata
        plays_processed: Number of play records processed
        play_metrics: Play-level statistics
        imported_count: Successfully imported/processed tracks
        exported_count: Tracks exported to external services
        filtered_count: Tracks intentionally filtered out (too short, incognito, etc.)
        duplicate_count: Tracks that already existed in database
        error_count: Tracks that failed processing
        already_liked: Tracks already in target state
        candidates: Total tracks considered for operation
        new_tracks_count: Canonical tracks created during import
        updated_tracks_count: Existing canonical tracks with new plays
    """

    tracks: list[Track] = field(factory=list)
    metrics: dict[str, dict[int, Any]] = field(
        factory=dict,
    )  # metric_name -> {track_id(int) -> value}
    operation_name: str = field(default="")
    execution_time: float = field(default=0.0)
    tracklist: "TrackList | None" = field(
        default=None
    )  # Optional tracklist with metadata

    # Play-based operation support
    plays_processed: int = field(default=0)  # Number of play records processed
    play_metrics: dict[str, Any] = field(factory=dict)  # Play-level statistics

    # Unified count fields (consolidated from specialized classes)
    # These fields are Optional - only operations that use them should set them
    imported_count: int | None = field(
        default=None
    )  # Tracks imported/processed successfully
    exported_count: int | None = field(
        default=None
    )  # Tracks exported to external service
    filtered_count: int | None = field(
        default=None
    )  # Plays filtered out (too short, incognito, etc.)
    duplicate_count: int | None = field(
        default=None
    )  # Plays that already existed in database
    error_count: int | None = field(default=None)  # Tracks that failed processing
    already_liked: int | None = field(default=None)  # Tracks already in desired state
    candidates: int | None = field(
        default=None
    )  # Total tracks considered for operation
    new_tracks_count: int | None = field(
        default=None
    )  # Canonical tracks created during import
    updated_tracks_count: int | None = field(
        default=None
    )  # Existing canonical tracks with new plays

    def get_metric(
        self,
        track_id: int | None,
        metric_name: str,
        default: Any = None,
    ) -> Any:
        """Get specific metric value for a track.

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

    @property
    def total_processed(self) -> int | None:
        """Returns sum of imported, exported, filtered, duplicate and error counts.

        Returns:
            Total processed items, or None if no counts were set
        """
        counts = [
            self.imported_count,
            self.exported_count,
            self.filtered_count,
            self.duplicate_count,
            self.error_count,
        ]
        # Only calculate if at least one count field has been set
        if all(count is None for count in counts):
            return None
        # Treat None as 0 for calculation
        return sum(count or 0 for count in counts)

    @property
    def attempted_to_process(self) -> int | None:
        """Returns count of items we actually attempted to process (excludes filtered).

        This is used for success rate calculation - filtered items are intentionally
        skipped and shouldn't count as processing attempts.

        Returns:
            Attempted processing count, or None if no counts were set
        """
        counts = [
            self.imported_count,
            self.exported_count,
            self.duplicate_count,
            self.error_count,
        ]
        # Only calculate if at least one count field has been set
        if all(count is None for count in counts):
            return None
        # Treat None as 0 for calculation
        return sum(count or 0 for count in counts)

    @property
    def success_rate(self) -> float | None:
        """Returns percentage of successful operations out of attempted processing.

        Uses attempted_to_process (excludes filtered) as denominator since filtered
        items are intentionally skipped, not processing failures.

        Returns:
            Success rate percentage, or None if no counts available
        """
        attempted = self.attempted_to_process
        if attempted is None or attempted == 0:
            return None
        imported = self.imported_count or 0
        exported = self.exported_count or 0
        return ((imported + exported) / attempted) * 100

    @property
    def efficiency_rate(self) -> float | None:
        """Returns percentage of tracks already in target state: already_liked / candidates * 100.

        Returns:
            Efficiency rate percentage, or None if no candidate count available
        """
        if self.candidates is None or self.candidates == 0:
            return None
        already_liked = self.already_liked or 0
        return (already_liked / self.candidates) * 100

    def to_dict(self) -> dict[str, Any]:
        """Converts result to JSON-serializable dictionary for API responses.

        Returns:
            Dictionary with operation stats, track details, and metrics summary
        """
        result = {
            "operation_name": self.operation_name,
            "execution_time": self.execution_time,
            "track_count": len(self.tracks),
            "tracks": [
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
            ],
            "metrics_summary": {
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
            },
        }

        # Only include unified count fields that have been set (not None)
        if self.imported_count is not None:
            result["imported_count"] = self.imported_count
        if self.exported_count is not None:
            result["exported_count"] = self.exported_count
        if self.filtered_count is not None:
            result["filtered_count"] = self.filtered_count
        if self.duplicate_count is not None:
            result["duplicate_count"] = self.duplicate_count
        if self.error_count is not None:
            result["error_count"] = self.error_count
        if self.already_liked is not None:
            result["already_liked"] = self.already_liked
        if self.candidates is not None:
            result["candidates"] = self.candidates

        # Only include computed properties if they're meaningful (not None)
        if self.total_processed is not None:
            result["total_processed"] = self.total_processed
        if self.success_rate is not None:
            result["success_rate"] = self.success_rate
        if self.efficiency_rate is not None:
            result["efficiency_rate"] = self.efficiency_rate

        # Add play-based metrics if this is a play operation
        if self.plays_processed > 0:
            result["plays_processed"] = self.plays_processed
            result["play_metrics"] = self.play_metrics.copy()

        return result


@define(frozen=False)
class WorkflowResult(OperationResult):
    """Operation result specialized for workflow execution tracking.

    Extends OperationResult with workflow-specific naming and factory methods
    while maintaining compatibility with existing workflow code.
    """

    @property
    def workflow_name(self) -> str:
        """Returns workflow name for backward compatibility."""
        return self.operation_name


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
