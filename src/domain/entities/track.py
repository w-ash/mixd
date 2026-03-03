"""Track-related domain entities.

Pure track representations and related value objects with zero external dependencies.
"""

# pyright: reportExplicitAny=false, reportAny=false
# Legitimate Any: service_metadata, raw_data dicts, factory patterns

from datetime import datetime
from typing import Any, Literal, Self, TypedDict, cast

import attrs
from attrs import define, field, validators

from .shared import (
    MetricValue,
    utc_now_factory,
)


@define(frozen=True, slots=True)
class Artist:
    """Artist representation with normalized metadata."""

    name: str = field(validator=validators.instance_of(str))


def _validate_artists(
    _instance: object,
    _attribute: attrs.Attribute[list[Artist]],
    value: list[Artist],
) -> None:
    """Validate artists list: non-empty and all elements are Artist instances."""
    if not value:
        raise ValueError("Track must have at least one artist")
    for artist in value:
        if not isinstance(artist, Artist):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise TypeError(f"Expected Artist, got {type(artist).__name__}")


@define(frozen=True, slots=True)
class Track:
    """Immutable track entity representing a musical recording.

    Tracks are the core entity in our domain model, containing
    essential metadata while supporting resolution to external connectors.
    """

    # Core metadata
    title: str = field(validator=validators.instance_of(str))
    artists: list[Artist] = field(
        factory=list,
        validator=_validate_artists,
    )
    album: str | None = field(default=None)
    duration_ms: int | None = field(default=None)
    release_date: datetime | None = field(default=None)
    isrc: str | None = field(default=None)

    # Extended properties
    id: int | None = field(default=None)
    connector_track_identifiers: dict[str, str] = field(factory=dict)
    connector_metadata: dict[str, dict[str, Any]] = field(factory=dict)

    def with_connector_track_id(self, connector: str, sid: str) -> Self:
        """Create a new track with additional connector identifier."""
        new_ids = self.connector_track_identifiers.copy()
        new_ids[connector] = sid
        return attrs.evolve(self, connector_track_identifiers=new_ids)

    def with_id(self, db_id: int) -> Self:
        """Set the internal database ID for this track."""
        if db_id <= 0:
            raise ValueError(
                f"Invalid database ID: {db_id}. Must be a positive integer.",
            )
        return attrs.evolve(self, id=db_id)

    def with_connector_metadata(
        self,
        connector: str,
        metadata: dict[str, Any],
    ) -> Self:
        """Create a new track with additional connector metadata."""
        new_metadata = self.connector_metadata.copy()
        new_metadata[connector] = {**new_metadata.get(connector, {}), **metadata}
        return attrs.evolve(self, connector_metadata=new_metadata)

    def is_liked_on(self, service: str) -> bool:
        """Check if track is liked/loved on the specified service."""
        return bool(self.connector_metadata.get(service, {}).get("is_liked", False))

    def get_connector_attribute(
        self,
        connector: str,
        attribute: str,
        default: object = None,
    ) -> object:
        """Get a specific attribute from connector metadata."""
        return self.connector_metadata.get(connector, {}).get(attribute, default)

    def has_same_identity_as(self, other: Track) -> bool:
        """Compare tracks by external identifiers for identity resolution.

        Business rule: tracks with identical external identifiers (ISRC,
        Spotify ID, etc.) represent the same song and can be merged.

        Args:
            other: Track to compare against.

        Returns:
            True if tracks have matching external identifiers.
        """
        # Check ISRC first - most reliable identifier
        if self.isrc and other.isrc and self.isrc == other.isrc:
            return True

        # Check connector track IDs for overlap
        for connector, my_id in self.connector_track_identifiers.items():
            other_id = other.connector_track_identifiers.get(connector)
            if other_id and my_id == other_id:
                return True

        return False


@define(frozen=True, slots=True)
class TrackLike:
    """Immutable representation of a track like/love interaction."""

    track_id: int
    service: str  # 'spotify', 'lastfm', 'narada'
    is_liked: bool = True  # Default to liked since most cases create likes
    liked_at: datetime | None = None
    last_synced: datetime | None = None
    id: int | None = None  # Database ID if available


@define(frozen=True, slots=True)
class TrackMetric:
    """Time-series metrics for tracks from external services."""

    track_id: int
    connector_name: str
    metric_type: str
    value: float
    collected_at: datetime = field(factory=utc_now_factory)
    id: int | None = None


@define(frozen=True, slots=True)
class ConnectorTrack:
    """External track representation from a specific music service."""

    connector_name: str
    connector_track_identifier: str
    title: str
    artists: list[Artist]
    album: str | None = None
    duration_ms: int | None = None
    isrc: str | None = None
    release_date: datetime | None = None
    raw_metadata: dict[str, Any] = field(factory=dict)
    last_updated: datetime = field(factory=utc_now_factory)
    id: int | None = None


@define(frozen=True, slots=True)
class ConnectorTrackMapping:
    """Cross-connected-service entity mapping with confidence scoring.

    Tracks how entities are resolved across connectors with metadata
    about match quality and resolution method.
    """

    connector_name: str = field(validator=validators.instance_of(str))
    connector_track_identifier: str = field(validator=validators.instance_of(str))
    match_method: str = field(
        validator=validators.in_([
            "direct",  # Direct match where internal object was created from the connector
            "isrc",  # Matched by ISRC
            "mbid",  # Matched by MusicBrainz ID
            "artist_title",  # Matched by artist and title
        ]),
    )
    confidence: int = field(
        validator=[validators.instance_of(int), validators.ge(0), validators.le(100)],
    )
    metadata: dict[str, Any] = field(factory=dict)


class TrackListMetadata(TypedDict, total=False):
    """Known metadata keys that flow through TrackList pipelines.

    All keys are optional (total=False). Workflow nodes write subsets of these
    keys as tracks flow through source → enricher → transform → destination.
    """

    # Enrichment data (written by enrichers, read by transforms)
    metrics: dict[str, dict[int, MetricValue]]  # metric_name → track_id → value
    fresh_metric_ids: dict[str, list[int]]  # metric_name → freshly-fetched track IDs

    # Source tracking (written by source nodes & combiners)
    track_sources: dict[
        int, dict[str, str]
    ]  # track_id → {playlist_name, source, source_id}
    operation: str  # "concatenate", "alternate", "get_played_tracks", etc.
    source_count: int  # number of tracklists combined
    source_playlist_name: str  # from Playlist → TrackList conversion
    added_at_dates: dict[int, str]  # track_id → ISO date (from PlaylistEntry.added_at)


# Valid keys for TrackList.metadata — used to constrain with_metadata/get_metadata
type MetadataKey = Literal[
    "metrics",
    "fresh_metric_ids",
    "track_sources",
    "operation",
    "source_count",
    "source_playlist_name",
    "added_at_dates",
]


@define(frozen=True, slots=True)
class TrackList:
    """Ephemeral, immutable collection of tracks for processing pipelines.

    Unlike Playlists, TrackLists are not persisted entities but rather
    intermediate processing artifacts that flow through transformation pipelines.
    """

    tracks: list[Track] = field(factory=list)
    metadata: TrackListMetadata = field(factory=TrackListMetadata)

    def with_tracks(self, tracks: list[Track]) -> Self:
        """Create new TrackList with the given tracks."""
        return self.__class__(
            tracks=tracks,
            metadata=self.metadata.copy(),
        )

    def with_metadata(self, key: MetadataKey, value: Any) -> Self:
        """Add metadata to the TrackList."""
        new_metadata: dict[str, Any] = dict(self.metadata)
        new_metadata[key] = value
        return self.__class__(
            tracks=self.tracks,
            metadata=cast(TrackListMetadata, new_metadata),
        )
