"""Core domain entities representing music concepts."""

# Track-related entities
# Operation-related entities
from .operations import (
    ConnectorTrackPlay,
    OperationResult,
    PlayRecord,
    SyncCheckpoint,
    SyncCheckpointStatus,
    TrackPlay,
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
from .shared import MetricValue, ensure_utc, utc_now_factory

# Summary metrics
from .summary_metrics import SummaryMetric, SummaryMetricCollection
from .track import (
    Artist,
    ConnectorTrack,
    ConnectorTrackMapping,
    MetadataKey,
    Track,
    TrackLike,
    TrackList,
    TrackListMetadata,
    TrackMetric,
)

# Track mapping entity
from .track_mapping import TrackMapping

__all__ = [
    "Artist",
    "ConnectorPlaylist",
    "ConnectorPlaylistItem",
    "ConnectorTrack",
    "ConnectorTrackMapping",
    "ConnectorTrackPlay",
    "MetadataKey",
    "MetricValue",
    "OperationResult",
    "PlayRecord",
    "Playlist",
    "PlaylistEntry",
    "SummaryMetric",
    "SummaryMetricCollection",
    "SyncCheckpoint",
    "SyncCheckpointStatus",
    "Track",
    "TrackLike",
    "TrackList",
    "TrackListMetadata",
    "TrackMapping",
    "TrackMetric",
    "TrackPlay",
    "create_lastfm_play_record",
    "ensure_utc",
    "utc_now_factory",
]
