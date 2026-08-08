"""Data structures for music service operations and synchronization tracking.

Contains classes for recording play events, sync progress, and operation results
from music services like Spotify and Last.fm.
"""

from collections.abc import Mapping
from datetime import datetime
from typing import TYPE_CHECKING, Final, Literal, Self
from uuid import UUID, uuid7

from attrs import define, field

if TYPE_CHECKING:
    from src.infrastructure.connectors.spotify.personal_data import SpotifyPlayRecord

from .shared import (
    JsonDict,
    JsonValue,
    MetricValue,
    empty_json_map,
    validate_timezone_aware,
)
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
    remote_total: int | None = None  # Total items reported by remote service
    # Adaptive-polling state (v0.10.1). ``last_timestamp`` says when the user
    # last listened; ``last_polled_at`` says when we last checked. Gating
    # freshness on the former would fire a poll on every page view of an account
    # that simply isn't playing anything.
    last_polled_at: datetime | None = None
    # Opaque to the entity — the poll policy owns its shape via PollState.
    poll_state: JsonDict = field(factory=empty_json_map)
    id: UUID = field(factory=uuid7)

    def with_update(
        self,
        timestamp: datetime,
        cursor: str | Unset | None = UNSET,
        remote_total: int | Unset | None = UNSET,
    ) -> Self:
        """Returns new checkpoint with updated timestamp and optional cursor.

        Poll state is carried through untouched: importers own the sync cursor,
        the poll policy owns the polling columns, and neither may clobber the
        other's fields by saving a whole checkpoint.

        Args:
            timestamp: Latest sync timestamp to record
            cursor: Pagination cursor — omit to preserve, pass None to clear
            remote_total: Total items in remote library — omit to preserve

        Returns:
            New checkpoint instance with updated values
        """
        return self.__class__(
            user_id=self.user_id,
            service=self.service,
            entity_type=self.entity_type,
            last_timestamp=timestamp,
            cursor=self.cursor if isinstance(cursor, Unset) else cursor,
            remote_total=self.remote_total
            if isinstance(remote_total, Unset)
            else remote_total,
            last_polled_at=self.last_polled_at,
            poll_state=self.poll_state,
            id=self.id,
        )


# Lives here rather than beside the poll policy that computes it: the entity
# carrying the value owns its vocabulary, and the domain service imports it back.
# The reverse direction would make entities depend on services.
type PollHealth = Literal["healthy", "overdue"]


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
    local_count: int | None = None
    remote_total: int | None = None
    # Adaptive-polling surface (v0.10.1), populated only for polled checkpoints
    # and left None elsewhere. ``poll_health`` cannot be derived client-side from
    # ``last_polled_at`` alone: under adaptive backoff a 20-hour-old check can be
    # healthy (daily floor) while a 3-hour-old one is broken (sole-observer cap),
    # so the effective interval travels with it.
    last_polled_at: datetime | None = None
    effective_interval_seconds: int | None = None
    poll_health: PollHealth | None = None
    # True when a poll came back with a completely full window, meaning plays may
    # already have aged out of it unrecoverably. Surfaced rather than logged: the
    # user's remedy (add a second observer) is a decision only they can make.
    possible_gap: bool = False

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
    service_metadata: Mapping[str, JsonValue] = field(factory=empty_json_map)

    # Import tracking
    api_page: int | None = None
    raw_data: Mapping[str, JsonValue] = field(factory=empty_json_map)


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
    played_at: datetime = field(validator=validate_timezone_aware)
    service: str  # "spotify", "lastfm"
    user_id: str = "default"
    album_name: str | None = None
    ms_played: int | None = None
    service_metadata: Mapping[str, JsonValue] = field(factory=empty_json_map)

    # Import tracking (for debugging and batch management)
    api_page: int | None = None
    raw_data: Mapping[str, JsonValue] = field(factory=empty_json_map)
    import_timestamp: datetime | None = field(
        default=None, validator=validate_timezone_aware
    )
    import_source: str | None = None  # "lastfm_api", "spotify_export"
    import_batch_id: str | None = None

    # Connector identification (auto-derived in __attrs_post_init__)
    connector_name: str = field(init=False)
    connector_track_identifier: str = field(init=False)

    # Resolution tracking (nullable until resolved)
    resolved_track_id: UUID | None = None
    resolved_at: datetime | None = field(
        default=None, validator=validate_timezone_aware
    )

    # Database persistence
    id: UUID = field(factory=uuid7)

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
        *,
        user_id: str,
        import_timestamp: datetime | None = None,
        import_batch_id: str | None = None,
    ) -> Self:
        """Create ConnectorTrackPlay from SpotifyPlayRecord for unified processing.

        Args:
            spotify_record: Parsed Spotify personal data record
            user_id: The mixd user this ledger row belongs to. Required (not
                defaulted) so tenancy is set at construction rather than by a
                second pass over the whole import — a 198k-record export cannot
                afford a rebuilt copy of every row just to stamp one field.
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
            user_id=user_id,
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

    track_id: UUID | None
    service: str
    played_at: datetime = field(validator=validate_timezone_aware)
    user_id: str = "default"
    ms_played: int | None = None
    context: Mapping[str, JsonValue] | None = None
    id: UUID = field(factory=uuid7)

    # Cross-source deduplication: which services contributed to this play record
    source_services: list[str] | None = None

    # Import tracking (service-agnostic)
    import_timestamp: datetime | None = field(
        default=None, validator=validate_timezone_aware
    )
    import_source: str | None = None  # "spotify_export", "lastfm_api", "manual"
    import_batch_id: str | None = None


@define(frozen=True, slots=True)
class PlaySource:
    """Membership edge: which ledger observation backs which canonical play.

    Materialized (the ``play_sources`` table) so projection idempotence is a
    mechanical no-op diff — an unchanged group produces zero writes — and
    provenance survives without parsing ``context``. One row per ledger
    observation (unique on ``connector_play_id`` per user): an observation
    contributes to exactly one canonical play.
    """

    user_id: str
    track_play_id: UUID
    connector_play_id: UUID
    id: UUID = field(factory=uuid7)


# Summary-metric names that count items an operation *finished* — the evidence
# that a run with errors still did real work (``is_partial_failure``). Only
# completed-item counters belong here; denominators (``raw_plays``,
# ``candidates``, ``total``) and no-op tallies (``already_liked``,
# ``already_loved``, ``filtered``) do not, because they are positive on a run
# where nothing landed. Current producers:
#   ``track_plays``  — play import, canonical plays created (orchestrator)
#   ``resolved``     — play resolution phase, connector plays resolved
#   ``imported``     — Spotify likes import; Last.fm import (``domain/results``)
#   ``exported``     — Last.fm likes export
_SUCCESS_METRIC_NAMES: Final[frozenset[str]] = frozenset({
    "track_plays",
    "resolved",
    "imported",
    "exported",
})

# Metadata keys carrying the per-item failures of a partially-failed run. The
# entity that carries the value owns its vocabulary: the producer (the play
# import orchestrator) and the consumer (the SSE seam, which turns each entry
# into a run issue) both name the keys from here rather than agreeing on a
# string literal twice. Read through :attr:`OperationResult.resolution_failures`.
RESOLUTION_FAILURES_KEY: Final = "resolution_failures"
RESOLUTION_FAILURES_TRUNCATED_KEY: Final = "resolution_failures_truncated"


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
    metadata: dict[str, JsonValue] = field(factory=empty_json_map)
    metrics: dict[str, dict[UUID, MetricValue]] = field(
        factory=dict,
    )  # Per-track operational metrics: metric_name -> {track_id -> value}
    tracklist: TrackList | None = field(
        default=None
    )  # Optional tracklist with metadata

    def get_metric(
        self,
        track_id: UUID | None,
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

    @property
    def is_failure(self) -> bool:
        """True if this result records a (soft) failure.

        Use cases that handle an error internally record it as an ``errors``
        summary metric and/or an ``error`` metadata key rather than raising,
        so a failed scheduled sync is not mistaken for a successful fire (see
        ``_shared/sync_targets.sync_result_failed``) and the web SSE seam can
        surface failure the same way the scheduler and CLI already do. This
        property is the single definition all three surfaces read.
        """
        return self.summary_metrics.get("errors", 0) > 0 or "error" in self.metadata

    @property
    def is_partial_failure(self) -> bool:
        """True if this result recorded per-item errors *and* landed real work.

        Distinguishes "the operation fully failed" from "the operation finished
        but some items couldn't be processed" — the audit row's ``partial``
        status. Deliberately a refinement of :attr:`is_failure`, never a
        replacement: every failure-*signal* consumer (the scheduler's
        ``sync_targets.sync_result_failed``, the CLI) keeps reading
        ``is_failure``, which stays true here.

        "Landed real work" is the allowlist in ``_SUCCESS_METRIC_NAMES``, not
        "any non-``errors`` metric": denominators like ``raw_plays``,
        ``candidates``, and ``total`` are positive even when nothing succeeded,
        so counting them would report a total failure as partial. A new
        operation that wants ``partial`` adds its completed-item counter to that
        set — the single place the convention is written down.
        """
        return self.is_failure and any(
            self.summary_metrics.get(name, 0) > 0 for name in _SUCCESS_METRIC_NAMES
        )

    @property
    def failure_message(self) -> str | None:
        """Human-readable reason for a soft failure, if one was recorded.

        Soft-failure handlers stash the exception text in ``metadata`` under
        ``error`` (str) or ``errors`` (list of str) while ``to_counts`` flattens
        only ``summary_metrics`` — so without this accessor the reason never
        leaves the process. The SSE seam persists it to the run's ``issues``.
        """
        error = self.metadata.get("error")
        if isinstance(error, str) and error:
            return error
        errors = self.metadata.get("errors")
        if isinstance(errors, list):
            parts = [e for e in errors if isinstance(e, str) and e]
            if parts:
                return "; ".join(parts)
        return None

    @property
    def resolution_failures(self) -> list[JsonDict]:
        """Per-item failure records this run kept, already user-legible.

        Producers cap the list (the audit row's ``issues`` is a JSONB column, not
        a log); :attr:`resolution_failures_truncated` says how many were dropped.
        Like :attr:`failure_message`, this exists because ``to_counts()`` flattens
        only ``summary_metrics`` — without an accessor the detail never leaves the
        process, and the user never learns *which* tracks failed.
        """
        failures = self.metadata.get(RESOLUTION_FAILURES_KEY)
        if not isinstance(failures, list):
            return []
        return [dict(f) for f in failures if isinstance(f, Mapping)]

    @property
    def resolution_failures_truncated(self) -> int:
        """How many per-item failures were seen but not recorded (0 if none)."""
        truncated = self.metadata.get(RESOLUTION_FAILURES_TRUNCATED_KEY)
        return truncated if isinstance(truncated, int) else 0

    def to_counts(self) -> dict[str, JsonValue]:
        """Flatten summary metrics into a ``name -> value`` mapping.

        Used by the SSE seam to populate the terminal event and the
        ``OperationRun`` audit row's JSONB counts from one source — the same
        metrics the CLI renders — so web counts can never drift from CLI.
        """
        return {m.name: m.value for m in self.summary_metrics.sorted()}

    def to_dict(
        self,
    ) -> dict[str, object]:  # JSON serialization boundary — heterogeneous output
        """Converts result to JSON-serializable dictionary for API responses.

        Returns:
            Dictionary with operation stats, summary metrics, track details, and per-track metrics
        """
        result: dict[str, object] = {
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
                    "id": str(t.id),
                    "title": t.title,
                    "artists": [a.name for a in t.artists],
                    "metrics": {
                        name: values.get(t.id)
                        for name, values in self.metrics.items()
                        if t.id in values
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
    raw_data: Mapping[str, JsonValue] | None = None,
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
    service_metadata: dict[str, JsonValue] = {
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
