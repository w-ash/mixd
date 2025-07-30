"""Data structures for music service operations and synchronization tracking.

Contains classes for recording play events, sync progress, and operation results
from music services like Spotify and Last.fm.
"""

from datetime import UTC, datetime
from typing import Any

from attrs import define, field

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

    def to_track_play(
        self,
        track_id: int | None = None,
        import_batch_id: str | None = None,
        import_timestamp: datetime | None = None,
    ) -> "TrackPlay":
        """Converts raw play data to normalized TrackPlay format.

        Args:
            track_id: Database ID of the track (if known)
            import_batch_id: Batch identifier for this import
            import_timestamp: When this data was imported

        Returns:
            Normalized TrackPlay with standardized field names
        """
        # Build standardized context using TrackContextFields
        context = {
            TrackContextFields.TRACK_NAME: self.track_name,
            TrackContextFields.ARTIST_NAME: self.artist_name,
        }

        if self.album_name:
            context[TrackContextFields.ALBUM_NAME] = self.album_name

        # Add service-specific metadata to context
        context.update(self.service_metadata)

        return TrackPlay(
            track_id=track_id,
            service=self.service,
            played_at=self.played_at,
            ms_played=self.ms_played,
            context=context,
            import_timestamp=import_timestamp or datetime.now(UTC),
            import_source=f"{self.service}_api",
            import_batch_id=import_batch_id,
        )


@define(frozen=True, slots=True)
class TrackPlay:
    """Normalized record of when a user played a track on any music service.

    Contains standardized play event data with service context preserved
    for deduplication and analytics.

    Args:
        track_id: Database ID of the track
        service: Source service ("spotify", "lastfm", etc.)
        played_at: When the track was played
        ms_played: Duration played in milliseconds
        context: Service metadata and behavioral data
        id: Database ID of this play record
        import_timestamp: When this play was imported
        import_source: How this play was imported (API, export file, etc.)
        import_batch_id: Batch identifier for bulk imports
    """

    track_id: int | None
    service: str
    played_at: datetime
    ms_played: int | None = None
    context: dict[str, Any] | None = None
    id: int | None = None

    # Import tracking (service-agnostic)
    import_timestamp: datetime | None = None
    import_source: str | None = None  # "spotify_export", "lastfm_api", "manual"
    import_batch_id: str | None = None

    def get_platform(self) -> str | None:
        """Returns the device/platform where track was played (Spotify data)."""
        return self.context.get("platform") if self.context else None

    def is_skipped(self) -> bool:
        """Returns True if user skipped the track before it finished (Spotify data)."""
        return self.context.get("skipped", False) if self.context else False

    def is_now_playing(self) -> bool:
        """Returns True if track is currently playing (Last.fm real-time data)."""
        return self.context.get("nowplaying", False) if self.context else False

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
        skipped_count: Tracks skipped (duplicates, etc.)
        error_count: Tracks that failed processing
        already_liked: Tracks already in target state
        candidates: Total tracks considered for operation
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
    skipped_count: int | None = field(
        default=None
    )  # Tracks skipped (already processed, etc)
    error_count: int | None = field(default=None)  # Tracks that failed processing
    already_liked: int | None = field(default=None)  # Tracks already in desired state
    candidates: int | None = field(
        default=None
    )  # Total tracks considered for operation

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

    def with_metric(
        self, metric_name: str, values: dict[int, Any]
    ) -> "OperationResult":
        """Add or update a metric, returning a new instance.

        Args:
            metric_name: Name of the metric to add/update
            values: Dictionary mapping track IDs to metric values

        Returns:
            New instance with the updated metric
        """
        metrics = self.metrics.copy()
        metrics[metric_name] = values
        return self.__class__(
            tracks=self.tracks,
            metrics=metrics,
            operation_name=self.operation_name,
            execution_time=self.execution_time,
            imported_count=self.imported_count,
            exported_count=self.exported_count,
            skipped_count=self.skipped_count,
            error_count=self.error_count,
            already_liked=self.already_liked,
            candidates=self.candidates,
        )

    @property
    def total_processed(self) -> int | None:
        """Returns sum of imported, exported, skipped and error counts.

        Returns:
            Total processed items, or None if no counts were set
        """
        counts = [
            self.imported_count,
            self.exported_count,
            self.skipped_count,
            self.error_count,
        ]
        # Only calculate if at least one count field has been set
        if all(count is None for count in counts):
            return None
        # Treat None as 0 for calculation
        return sum(count or 0 for count in counts)

    @property
    def success_rate(self) -> float | None:
        """Returns percentage of successful operations (imported + exported) / total * 100.

        Returns:
            Success rate percentage, or None if no counts available
        """
        total = self.total_processed
        if total is None or total == 0:
            return None
        imported = self.imported_count or 0
        exported = self.exported_count or 0
        return ((imported + exported) / total) * 100

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
        if self.skipped_count is not None:
            result["skipped_count"] = self.skipped_count
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

    @classmethod
    def create_workflow_result(
        cls,
        tracks: list[Track],
        metrics: dict[str, dict[int, Any]] | None = None,
        workflow_name: str = "",
        execution_time: float = 0.0,
    ) -> "WorkflowResult":
        """Creates initialized WorkflowResult with workflow-specific defaults.

        Args:
            tracks: List of tracks processed by the workflow
            metrics: Optional per-track metrics dictionary
            workflow_name: Name of the executed workflow
            execution_time: Time taken to execute the workflow in seconds

        Returns:
            Initialized WorkflowResult instance
        """
        return cls(
            tracks=tracks,
            metrics=metrics or {},
            operation_name=workflow_name,
            execution_time=execution_time,
        )


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
