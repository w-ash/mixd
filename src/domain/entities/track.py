"""Track-related domain entities.

Pure track representations and related value objects with zero external dependencies.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Literal, Protocol, Self, TypedDict, cast, overload
from uuid import UUID, uuid7

import attrs
from attrs import define, field, validators

from .preference import TrackPreference
from .shared import (
    JsonValue,
    MetricValue,
    empty_json_map,
    utc_now_factory,
)
from .tag import TrackTag


@define(frozen=True, slots=True)
class ArtistCredit:
    """One artist credit on a track: the credited spelling plus optional identity.

    ``credited_name`` keeps the spelling on the record; ``artist_id`` links the
    credit to a canonical artist when one is resolved (``None`` is valid — a
    compilation credit or an unresolved name keeps its row). ``join_phrase``
    is the connective between this credit and the next (" & ", " feat. ");
    ``role`` is set only when the source states one.
    """

    credited_name: str = field(validator=validators.instance_of(str))
    artist_id: UUID | None = field(
        default=None, validator=validators.optional(validators.instance_of(UUID))
    )
    join_phrase: str | None = field(
        default=None, validator=validators.optional(validators.instance_of(str))
    )
    role: str | None = field(
        default=None, validator=validators.optional(validators.instance_of(str))
    )


@define(frozen=True, slots=True)
class ConnectorArtistCredit:
    """One artist credit as a service states it on its own track record.

    The connector twin of :class:`ArtistCredit`. ``connector_artist_identifier``
    is the service's own artist id (a Spotify artist id, a MusicBrainz artist
    MBID, a Last.fm name) and never a canonical ``artists.id`` — a connector
    track is a global record and must not point into one tenant's library.
    ``None`` is valid: Apple's song payload names its artist without an id.
    """

    credited_name: str = field(validator=validators.instance_of(str))
    connector_artist_identifier: str | None = field(
        default=None, validator=validators.optional(validators.instance_of(str))
    )
    join_phrase: str | None = field(
        default=None, validator=validators.optional(validators.instance_of(str))
    )
    role: str | None = field(
        default=None, validator=validators.optional(validators.instance_of(str))
    )


class Credited(Protocol):
    """What a credit must expose to be displayed: its name and its connective."""

    @property
    def credited_name(self) -> str: ...

    @property
    def join_phrase(self) -> str | None: ...


def credits_display(credits: Sequence[Credited]) -> str:
    """Concatenate credited names, joined by each credit's join phrase or ", ".

    One implementation for canonical and connector credits alike. The last
    credit's join phrase is never emitted — there is nothing after it to join.
    """
    parts: list[str] = []
    last = len(credits) - 1
    for index, credit in enumerate(credits):
        parts.append(credit.credited_name)
        if index < last:
            parts.append(credit.join_phrase or ", ")
    return "".join(parts)


def _validate_artists(
    _instance: object,
    _attribute: attrs.Attribute[tuple[ArtistCredit, ...]],
    value: tuple[ArtistCredit, ...],
) -> None:
    """Validate credits: non-empty and all elements are ArtistCredit instances."""
    if not value:
        raise ValueError("Track must have at least one artist")
    for credit in value:
        if not isinstance(credit, ArtistCredit):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise TypeError(f"Expected ArtistCredit, got {type(credit).__name__}")


def _validate_connector_credits(
    _instance: object,
    _attribute: attrs.Attribute[tuple[ConnectorArtistCredit, ...]],
    value: tuple[object, ...],
) -> None:
    """A connector track's credits are connector credits, never canonical ones.

    Typed over ``object`` on purpose: the converter accepts any iterable, so
    what arrives at runtime is whatever the caller passed.
    """
    for credit in value:
        if not isinstance(credit, ConnectorArtistCredit):
            raise TypeError(
                f"Expected ConnectorArtistCredit, got {type(credit).__name__}"
            )


@define(frozen=True, slots=True)
class Track:
    """Immutable track entity representing a musical recording.

    Tracks are the core entity in our domain model, containing
    essential metadata while supporting resolution to external connectors.
    """

    # Core metadata
    title: str = field(validator=validators.instance_of(str))
    artists: tuple[ArtistCredit, ...] = field(
        factory=tuple,
        converter=tuple,
        validator=_validate_artists,
    )
    album: str | None = field(default=None)
    duration_ms: int | None = field(default=None)
    release_date: datetime | None = field(default=None)
    isrc: str | None = field(default=None)

    # Tenant is always explicit: no default at any layer (v0.12.0.2).
    user_id: str = field(kw_only=True)

    # Extended properties
    id: UUID = field(factory=uuid7)
    version: int = 0  # 0 = unpersisted, ≥1 = persisted (set by repository on load/save)
    # Play aggregates: read-only on load; persisting a Track never writes them.
    play_count: int = 0
    last_played_at: datetime | None = None
    first_played_at: datetime | None = None
    connector_track_identifiers: dict[str, str] = field(factory=dict)
    connector_metadata: Mapping[str, Mapping[str, JsonValue]] = field(
        factory=dict[str, Mapping[str, JsonValue]]
    )

    @property
    def artists_display(self) -> str:
        """Credited names joined for display; also the ``artists_text`` column value."""
        return credits_display(self.artists)

    def with_connector_track_id(self, connector: str, sid: str) -> Self:
        """Create a new track with additional connector identifier."""
        new_ids = self.connector_track_identifiers.copy()
        new_ids[connector] = sid
        return attrs.evolve(self, connector_track_identifiers=new_ids)

    def with_connector_metadata(
        self,
        connector: str,
        metadata: Mapping[str, JsonValue],
    ) -> Self:
        """Create a new track with additional connector metadata."""
        new_metadata: dict[str, Mapping[str, JsonValue]] = dict(self.connector_metadata)
        new_metadata[connector] = {**dict(new_metadata.get(connector, {})), **metadata}
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
        """Pre-persistence identity resolution — used by matching/resolution system.

        NOT used for pipeline deduplication. In-pipeline identity is always
        canonical track.id (database primary key). Use ``filter_duplicates()``
        for pipeline dedup, which relies on ``track.id``.

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
    """A track liked on one service.

    A like is a presence row: it exists while the track is liked on the
    service and is deleted when it is not. There is no tombstone state.
    """

    track_id: UUID
    service: str  # 'spotify', 'lastfm', 'mixd'
    user_id: str
    liked_at: datetime | None = None
    updated_at: datetime | None = None
    id: UUID = field(factory=uuid7)


@define(frozen=True, slots=True)
class TrackMetric:
    """Time-series metrics for tracks from external services."""

    track_id: UUID
    connector_name: str
    metric_type: str
    value: float
    # Tenant is always explicit: no default at any layer (v0.12.0.2).
    user_id: str = field(kw_only=True)
    collected_at: datetime = field(factory=utc_now_factory)
    id: UUID = field(factory=uuid7)


@define(frozen=True, slots=True)
class ConnectorTrack:
    """External track representation from a specific music service."""

    connector_name: str
    connector_track_identifier: str
    title: str
    artists: tuple[ConnectorArtistCredit, ...] = field(
        converter=tuple, validator=_validate_connector_credits
    )
    album: str | None = None
    duration_ms: int | None = None
    isrc: str | None = None
    release_date: datetime | None = None
    raw_metadata: Mapping[str, JsonValue] = field(factory=empty_json_map)
    last_updated: datetime = field(factory=utc_now_factory)
    id: UUID = field(factory=uuid7)

    @property
    def artists_display(self) -> str:
        """Credited names joined for display; also the ``artists_text`` column value."""
        return credits_display(self.artists)


class TrackListMetadata(TypedDict, total=False):
    """Known metadata keys that flow through TrackList pipelines.

    All keys are optional (total=False). Workflow nodes write subsets of these
    keys as tracks flow through source → enricher → transform → destination.
    """

    # Enrichment data (written by enrichers, read by transforms)
    metrics: dict[str, dict[UUID, MetricValue]]  # metric_name → track_id → value
    fresh_metric_ids: dict[str, list[UUID]]  # metric_name → freshly-fetched track IDs
    preferences: dict[UUID, TrackPreference]  # track_id → preference (unrated omitted)
    tags: dict[UUID, list[TrackTag]]  # track_id → tags (untagged omitted)

    # Source tracking (written by source nodes & combiners)
    track_sources: dict[
        UUID, dict[str, str]
    ]  # track_id → {playlist_name, source, source_id}
    operation: str  # "concatenate", "alternate", "get_played_tracks", etc.
    source_count: int  # number of tracklists combined
    source_playlist_name: str  # from Playlist → TrackList conversion
    added_at_dates: dict[UUID, str]  # track_id → ISO date (from PlaylistEntry.added_at)
    favorite_artist_ids: frozenset[UUID]  # written by enricher.artist_favorites


# Valid keys for TrackList.metadata — used to constrain with_metadata/get_metadata
type MetadataKey = Literal[
    "metrics",
    "fresh_metric_ids",
    "preferences",
    "tags",
    "track_sources",
    "operation",
    "source_count",
    "source_playlist_name",
    "added_at_dates",
    "favorite_artist_ids",
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

    @overload
    def with_metadata(
        self, key: Literal["metrics"], value: dict[str, dict[UUID, MetricValue]]
    ) -> Self: ...
    @overload
    def with_metadata(
        self, key: Literal["fresh_metric_ids"], value: dict[str, list[UUID]]
    ) -> Self: ...
    @overload
    def with_metadata(
        self, key: Literal["preferences"], value: dict[UUID, TrackPreference]
    ) -> Self: ...
    @overload
    def with_metadata(
        self, key: Literal["tags"], value: dict[UUID, list[TrackTag]]
    ) -> Self: ...
    @overload
    def with_metadata(
        self, key: Literal["track_sources"], value: dict[UUID, dict[str, str]]
    ) -> Self: ...
    @overload
    def with_metadata(self, key: Literal["operation"], value: str) -> Self: ...
    @overload
    def with_metadata(self, key: Literal["source_count"], value: int) -> Self: ...
    @overload
    def with_metadata(
        self, key: Literal["source_playlist_name"], value: str
    ) -> Self: ...
    @overload
    def with_metadata(
        self, key: Literal["added_at_dates"], value: dict[UUID, str]
    ) -> Self: ...
    @overload
    def with_metadata(
        self, key: Literal["favorite_artist_ids"], value: frozenset[UUID]
    ) -> Self: ...
    def with_metadata(self, key: MetadataKey, value: object) -> Self:
        """Add metadata to the TrackList. Overloads enforce key-specific value types."""
        new_metadata: dict[str, object] = dict(self.metadata)
        new_metadata[key] = value
        return self.__class__(
            tracks=self.tracks,
            metadata=cast(TrackListMetadata, new_metadata),
        )
