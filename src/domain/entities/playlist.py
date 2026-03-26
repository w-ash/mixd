"""Playlist-related domain entities.

Pure playlist representations and related value objects with zero external dependencies.
"""

# pyright: reportExplicitAny=false
# Legitimate Any: service_metadata, raw_data dicts, factory patterns

from datetime import UTC, datetime
from typing import Any, Final, Self

from attrs import define, evolve, field, validators

from .shared import utc_now_factory
from .track import Track, TrackList

# Pseudo-connector name for internal DB track IDs (filtered from API responses)
DB_PSEUDO_CONNECTOR: Final = "db"


@define(frozen=True, slots=True)
class PlaylistEntry:
    """A track's membership in a playlist with position-specific metadata.

    Represents the relationship between a Track and a Playlist, capturing
    when and by whom the track was added. Enables temporal analytics and
    position-aware operations.

    Domain Semantics:
    - Track = song identity (immutable attributes like title, artist)
    - PlaylistEntry = membership instance (relationship metadata)

    The same Track can appear multiple times with different PlaylistEntry
    instances, each with independent added_at timestamps and positions.
    """

    track: Track
    added_at: datetime | None = None  # When added to THIS playlist
    added_by: str | None = None  # Who added it (user ID or service)


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
    extras: dict[str, Any] = field(factory=dict)


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
    id: int | None = field(default=None)
    # External service IDs (spotify, apple_music, etc) - NOT for internal DB ID
    connector_playlist_identifiers: dict[str, str] = field(factory=dict)
    # Additional metadata for playlist management (snapshot IDs, sync state, etc.)
    metadata: dict[str, Any] = field(factory=dict)
    # DB timestamp for when playlist was last modified
    updated_at: datetime | None = field(default=None)
    # Denormalized count for list views (avoids loading all entries just to count)
    track_count: int = field(default=0)

    @property
    def tracks(self) -> list[Track]:
        """Extract tracks without position metadata (convenience property)."""
        return [entry.track for entry in self.entries]

    def to_tracklist(self) -> TrackList:
        """Convert to TrackList for workflow processing.

        Preserves temporal metadata (added_at dates) so downstream transforms
        can filter/sort by when tracks were added to the playlist.
        """
        added_at_dates = {
            entry.track.id: entry.added_at.isoformat()
            for entry in self.entries
            if entry.track.id is not None and entry.added_at is not None
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

    def with_connector_playlist_id(
        self,
        connector: str,
        external_id: str,
    ) -> Self:
        """Create a new playlist with additional connector identifier.

        Args:
            connector: The name of the external service ("spotify", "apple_music", etc)
                       Do not use "db" or "internal" here - use the id field for that.
            external_id: The ID of this playlist in the external service
        """
        if connector in (DB_PSEUDO_CONNECTOR, "internal"):
            raise ValueError(
                f"Cannot use '{connector}' as connector name - use the id field instead",
            )

        # Python 3.13+ dict merge operator
        new_ids = self.connector_playlist_identifiers | {connector: external_id}
        return evolve(self, connector_playlist_identifiers=new_ids)

    def with_id(self, db_id: int) -> Self:
        """Set the internal database ID for this playlist.

        This is the source of truth for playlist identity in our system.
        """
        if db_id <= 0:
            raise ValueError(
                f"Invalid database ID: {db_id}. Must be a positive integer.",
            )

        return evolve(self, id=db_id)

    def with_metadata(self, metadata: dict[str, Any]) -> Self:
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
    raw_metadata: dict[str, Any] = field(factory=dict)
    last_updated: datetime = field(factory=utc_now_factory)
    id: int | None = None

    @property
    def track_ids(self) -> list[str]:
        """Get all track IDs in this playlist."""
        return [item.connector_track_identifier for item in self.items]
