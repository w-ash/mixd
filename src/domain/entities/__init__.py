"""Core domain entities representing music concepts."""

# Track-related entities
# Operation-related entities
from .operations import (
    ConnectorTrackPlay,
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
    PlaylistEntry,
)

# Shared utilities
from .shared import ensure_utc, utc_now_factory

# Summary metrics
from .summary_metrics import SummaryMetric, SummaryMetricCollection
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
    "ConnectorTrackPlay",
    # Operation entities
    "OperationResult",
    "PlayRecord",
    "Playlist",
    "PlaylistEntry",
    # Summary metrics
    "SummaryMetric",
    "SummaryMetricCollection",
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
    "utc_now_factory",
]
