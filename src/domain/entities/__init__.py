"""Core domain entities representing music concepts."""

# Track-related entities
# Operation-related entities
from .operations import (
    OperationResult,
    PlayRecord,
    SyncCheckpoint,
    SyncCheckpointStatus,
    TrackContextFields,
    TrackPlay,
    WorkflowResult,
    create_lastfm_play_record,
)

# Playlist-related entities
from .playlist import (
    ConnectorPlaylist,
    ConnectorPlaylistItem,
    Playlist,
    PlaylistTrack,
)

# Shared utilities
from .shared import ensure_utc
from .track import (
    Artist,
    ConnectorTrack,
    ConnectorTrackMapping,
    Track,
    TrackLike,
    TrackList,
    TrackMetric,
)

__all__ = [
    # Track entities
    "Artist",
    # Playlist entities
    "ConnectorPlaylist",
    "ConnectorPlaylistItem",
    "ConnectorTrack",
    "ConnectorTrackMapping",
    # Operation entities
    "OperationResult",
    "PlayRecord",
    "Playlist",
    "PlaylistTrack",
    "SyncCheckpoint",
    "SyncCheckpointStatus",
    "Track",
    "TrackContextFields",
    "TrackLike",
    "TrackList",
    "TrackMetric",
    "TrackPlay",
    "WorkflowResult",
    "create_lastfm_play_record",
    # Shared utilities
    "ensure_utc",
]
