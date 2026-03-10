"""Playlist link entity — typed relationship between canonical and external playlists.

Replaces the untyped dict[str, Any] that PlaylistMappingMapper previously produced.
Each PlaylistLink captures the connector relationship plus sync direction and status.
"""

from datetime import datetime
from enum import Enum

from attrs import define, field

from .shared import utc_now_factory


class SyncDirection(Enum):
    """Direction of sync between canonical and external playlists."""

    PUSH = "push"  # Canonical → External
    PULL = "pull"  # External → Canonical


class SyncStatus(Enum):
    """Current sync state of a playlist link."""

    NEVER_SYNCED = "never_synced"
    SYNCED = "synced"
    SYNCING = "syncing"
    ERROR = "error"


@define(frozen=True, slots=True)
class PlaylistLink:
    """Typed relationship between a canonical playlist and an external service playlist.

    Each link represents a one-to-one mapping with an explicit sync direction:
    - PUSH: canonical is truth, changes flow to external service
    - PULL: external is truth, changes flow to canonical playlist

    No bidirectional sync — direction is explicitly chosen by the user.
    """

    playlist_id: int
    connector_name: str
    connector_playlist_identifier: str  # External service's playlist ID
    connector_playlist_name: str | None = None  # Denormalized for display
    sync_direction: SyncDirection = SyncDirection.PUSH
    sync_status: SyncStatus = SyncStatus.NEVER_SYNCED
    last_synced: datetime | None = None
    last_sync_error: str | None = None
    last_sync_tracks_added: int | None = None
    last_sync_tracks_removed: int | None = None
    created_at: datetime = field(factory=utc_now_factory)
    id: int | None = None
