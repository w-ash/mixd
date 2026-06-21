"""Playlist-related domain entities.

Pure playlist representations and related value objects with zero external dependencies.
"""

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Final, Self
from uuid import UUID, uuid7

from attrs import define, evolve, field, validators

from .shared import JsonValue, empty_json_map, utc_now_factory
from .track import Track, TrackList

# Pseudo-connector name for internal DB track IDs (filtered from API responses)
DB_PSEUDO_CONNECTOR: Final = "db"

# Canonical connector identifiers — keep external service names in one place
# so use cases and connector resolvers can't drift on capitalization.
SPOTIFY_CONNECTOR: Final = "spotify"


@define(frozen=True, slots=True)
class ConnectorTrackRef:
    """Pointer to an external (connector) track with no canonical match yet.

    Carried by an UNRESOLVED ``PlaylistEntry`` so the playlist position is
    preserved with enough display data to render it ("Couldn't match: …") and
    enough identity to re-resolve it later, once a track mapping for
    ``(connector_name, connector_track_identifier)`` appears. Pure value object —
    no canonical ``Track``.
    """

    connector_name: str
    connector_track_identifier: str
    title: str | None = None
    artists: tuple[str, ...] = ()

    def to_metadata(self) -> dict[str, JsonValue]:
        """Serialize to the unresolved-row display/re-resolution snapshot.

        The single source of truth for the snapshot's key shape — paired with
        ``from_metadata`` so the persisted dict and its parse can't drift.
        """
        return {
            "connector_name": self.connector_name,
            "connector_track_identifier": self.connector_track_identifier,
            "title": self.title,
            "artists": list(self.artists),
        }

    @classmethod
    def from_metadata(cls, meta: Mapping[str, JsonValue] | None) -> Self | None:
        """Rebuild from a stored snapshot, narrowing defensively at the boundary.

        A malformed/empty snapshot (missing the connector identity) yields None.
        """
        if not isinstance(meta, Mapping):
            return None
        name = meta.get("connector_name")
        identifier = meta.get("connector_track_identifier")
        if not isinstance(name, str) or not isinstance(identifier, str):
            return None
        title = meta.get("title")
        artists = meta.get("artists")
        return cls(
            connector_name=name,
            connector_track_identifier=identifier,
            title=title if isinstance(title, str) else None,
            artists=tuple(a for a in artists if isinstance(a, str))
            if isinstance(artists, list)
            else (),
        )


@define(frozen=True, slots=True)
class PlaylistEntry:
    """A track's membership in a playlist with position-specific metadata.

    Represents the relationship between a Track and a Playlist, capturing
    when and by whom the track was added. Enables temporal analytics and
    position-aware operations.

    Domain Semantics:
    - Track = song identity (immutable attributes like title, artist)
    - PlaylistEntry = membership instance (relationship metadata)

    A membership is RESOLVED (``track`` set) or UNRESOLVED (``track`` is None,
    ``connector_track_ref`` set) — the latter being a source position whose
    connector track could not be matched/ingested. Keeping unresolved positions
    in the same ordered list is what makes an imported playlist always complete
    (right count + order); they re-resolve in place without losing their slot.

    The same Track can appear multiple times with different PlaylistEntry
    instances, each with independent added_at timestamps and positions.
    """

    track: Track | None = None
    added_at: datetime | None = None  # When added to THIS playlist
    added_by: str | None = None  # Who added it (user ID or service)
    # Set on unresolved entries (track is None). May also ride along a resolved
    # entry as provenance, but is required when the entry has no canonical track.
    connector_track_ref: ConnectorTrackRef | None = None
    # Stable membership identity. Mirrors ``DBPlaylistTrack.id`` when loaded
    # from the database; a fresh value for entries built in memory. ``eq=False``
    # keeps value-based equality (track + metadata) so the diff fast-path and
    # append dedupe are unaffected; the id is purely an addressing key for
    # identity-preserving reorder/remove and unresolved-entry re-resolution.
    id: UUID = field(factory=uuid7, eq=False)

    @property
    def is_resolved(self) -> bool:
        """True when this membership points at a canonical track."""
        return self.track is not None

    @property
    def display_title(self) -> str:
        """A title for UI/logging, from the track or the unresolved source ref."""
        if self.track is not None:
            return self.track.title
        if self.connector_track_ref is not None and self.connector_track_ref.title:
            return self.connector_track_ref.title
        return "Unknown track"


@define(frozen=True, slots=True)
class ConnectorPlaylistItem:
    """Represents a track within an external service playlist with its position metadata."""

    # Track identity - just the ID, not the full object
    connector_track_identifier: str

    # Position information
    position: int
    added_at: str | None = None
    added_by_id: str | None = None

    # Any service-specific data
    extras: Mapping[str, JsonValue] = field(factory=empty_json_map)


@define(frozen=True, slots=True)
class Playlist:
    """Persistent playlist entity with position-aware track memberships.

    Playlists are persistent entities that maintain track ordering along with
    position-specific metadata (when added, by whom). Unlike TrackLists which
    are ephemeral processing artifacts, Playlists represent stored, user-facing
    collections with full temporal history.

    Key Distinction:
    - TrackList: Ephemeral, workflow processing (just tracks)
    - Playlist: Persistent, database entity (entries with metadata)
    """

    name: str = field(validator=validators.instance_of(str))
    user_id: str = "default"
    entries: list[PlaylistEntry] = field(factory=list)
    description: str | None = field(default=None)
    # The internal database ID - source of truth for our system
    id: UUID = field(factory=uuid7)
    # External service IDs (spotify, apple_music, etc) - NOT for internal DB ID
    connector_playlist_identifiers: dict[str, str] = field(factory=dict)
    # Additional metadata for playlist management (snapshot IDs, sync state, etc.)
    metadata: Mapping[str, JsonValue] = field(factory=empty_json_map)
    # DB timestamp for when playlist was last modified
    updated_at: datetime | None = field(default=None)
    # Denormalized count for list views (avoids loading all entries just to count)
    track_count: int = field(default=0)

    @property
    def tracks(self) -> list[Track]:
        """Resolved canonical tracks only (unresolved entries carry no track).

        Diff/push/workflow paths operate on this — they only ever act on real
        canonical tracks — while ``entries``/``track_count`` keep the complete
        set so the playlist's count and order stay faithful to the source.
        """
        return [entry.track for entry in self.entries if entry.track is not None]

    @property
    def resolved_entries(self) -> list[PlaylistEntry]:
        """Entries that resolved to a canonical track."""
        return [entry for entry in self.entries if entry.track is not None]

    @property
    def unresolved_entries(self) -> list[PlaylistEntry]:
        """Entries whose connector track had no canonical match (preserved positions)."""
        return [entry for entry in self.entries if entry.track is None]

    @property
    def unresolved_count(self) -> int:
        """How many positions are unresolved (the "N unresolved" badge)."""
        return sum(1 for entry in self.entries if entry.track is None)

    def to_tracklist(self) -> TrackList:
        """Convert to TrackList for workflow processing.

        Preserves temporal metadata (added_at dates) so downstream transforms
        can filter/sort by when tracks were added to the playlist. Unresolved
        entries are skipped — a TrackList is canonical tracks only.
        """
        added_at_dates = {
            entry.track.id: entry.added_at.isoformat()
            for entry in self.entries
            if entry.track is not None and entry.added_at is not None
        }
        return TrackList(
            tracks=self.tracks,
            metadata={
                "source_playlist_name": self.name,
                "added_at_dates": added_at_dates,
            },
        )

    @classmethod
    def from_tracklist(
        cls,
        name: str,
        tracklist: TrackList | list[Track],
        added_at: datetime | None = None,
        description: str | None = None,
        connector_playlist_identifiers: dict[str, str] | None = None,
    ) -> Self:
        """Create Playlist from TrackList or list of Tracks with uniform added_at timestamp.

        Args:
            name: Playlist name
            tracklist: TrackList or list[Track] to convert
            added_at: Timestamp for all entries (defaults to now)
            description: Optional playlist description
            connector_playlist_identifiers: Optional connector IDs (spotify, apple_music, etc)

        Returns:
            New Playlist with entries
        """
        # Handle both TrackList and list[Track] for convenience
        if isinstance(tracklist, list):
            from src.domain.entities.track import TrackList

            tracklist = TrackList(tracks=tracklist)

        added_at = added_at or datetime.now(UTC)
        return cls(
            name=name,
            entries=[
                PlaylistEntry(track=t, added_at=added_at) for t in tracklist.tracks
            ],
            description=description,
            connector_playlist_identifiers=connector_playlist_identifiers or {},
        )

    def with_entries(self, entries: list[PlaylistEntry]) -> Self:
        """Create new playlist with updated entries."""
        return evolve(self, entries=entries)

    def with_metadata(self, metadata: Mapping[str, JsonValue]) -> Self:
        """Create new playlist with updated metadata.

        Args:
            metadata: Dictionary of metadata to attach to playlist

        Returns:
            New Playlist with updated metadata
        """
        return evolve(self, metadata=metadata)


@define(frozen=True, slots=True)
class ConnectorPlaylist:
    """External service-specific playlist representation."""

    connector_name: str
    connector_playlist_identifier: str
    name: str
    description: str | None = None
    items: list[ConnectorPlaylistItem] = field(
        factory=list
    )  # Single field for track items
    owner: str | None = None
    owner_id: str | None = None
    is_public: bool = False
    collaborative: bool = False
    follower_count: int | None = None
    raw_metadata: Mapping[str, JsonValue] = field(factory=empty_json_map)
    snapshot_id: str | None = None
    last_updated: datetime = field(factory=utc_now_factory)
    id: UUID = field(factory=uuid7)

    @property
    def track_ids(self) -> list[str]:
        """Get all track IDs in this playlist."""
        return [item.connector_track_identifier for item in self.items]
