"""Playlist-related domain entities.

Pure playlist representations and related value objects with zero external dependencies.
"""

from datetime import UTC, datetime
from typing import Any

from attrs import define, field, validators

from .track import Track


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
    """Playlists are persistent entities with DB/API identity and metadata.

    Playlists are a representation of a user-facing list of tracks to be played,
    stored sources or destinations for track collections. Unlike TrackLists, Playlists
    are persisted entities that can be shared, stored, and retrieved.

    Playlists maintain track ordering while supporting additional metadata and
    cross-connector playlist identifiers for resolution across
    different music services.
    """

    name: str = field(validator=validators.instance_of(str))
    tracks: list[Track] = field(factory=list)
    description: str | None = field(default=None)
    # The internal database ID - source of truth for our system
    id: int | None = field(default=None)
    # External service IDs (spotify, apple_music, etc) - NOT for internal DB ID
    connector_playlist_identifiers: dict[str, str] = field(factory=dict)
    # Additional metadata for playlist management (snapshot IDs, sync state, etc.)
    metadata: dict[str, Any] = field(factory=dict)

    def with_tracks(self, tracks: list[Track]) -> "Playlist":
        """Create a new playlist with the given tracks."""
        return self.__class__(
            name=self.name,
            tracks=tracks,
            description=self.description,
            id=self.id,
            connector_playlist_identifiers=self.connector_playlist_identifiers.copy(),
            metadata=self.metadata.copy(),
        )

    def with_connector_playlist_id(
        self,
        connector: str,
        external_id: str,
    ) -> "Playlist":
        """Create a new playlist with additional connector identifier.

        Args:
            connector: The name of the external service ("spotify", "apple_music", etc)
                       Do not use "db" or "internal" here - use the id field for that.
            external_id: The ID of this playlist in the external service
        """
        if connector in ("db", "internal"):
            raise ValueError(
                f"Cannot use '{connector}' as connector name - use the id field instead",
            )

        new_ids = self.connector_playlist_identifiers.copy()
        new_ids[connector] = external_id

        return self.__class__(
            name=self.name,
            tracks=self.tracks,
            description=self.description,
            id=self.id,
            connector_playlist_identifiers=new_ids,
            metadata=self.metadata.copy(),
        )

    def with_id(self, db_id: int) -> "Playlist":
        """Set the internal database ID for this playlist.

        This is the source of truth for playlist identity in our system.
        """
        if not isinstance(db_id, int) or db_id <= 0:
            raise ValueError(
                f"Invalid database ID: {db_id}. Must be a positive integer.",
            )

        return self.__class__(
            name=self.name,
            tracks=self.tracks,
            description=self.description,
            id=db_id,
            connector_playlist_identifiers=self.connector_playlist_identifiers.copy(),
            metadata=self.metadata.copy(),
        )


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
    last_updated: datetime = field(factory=lambda: datetime.now(UTC))
    id: int | None = None

    @property
    def track_ids(self) -> list[str]:
        """Get all track IDs in this playlist."""
        return [item.connector_track_identifier for item in self.items]


@define(frozen=True, slots=True)
class PlaylistTrack:
    """Playlist track ordering and metadata."""

    playlist_id: int
    track_id: int
    sort_key: str
    added_at: datetime | None = None
    id: int | None = None
