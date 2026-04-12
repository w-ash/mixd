"""Core domain entities representing music concepts."""

# Track-related entities
# Operation-related entities
# Integrity monitoring
from .integrity import CheckStatus, IntegrityCheckResult, IntegrityReport
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
    DB_PSEUDO_CONNECTOR,
    ConnectorPlaylist,
    ConnectorPlaylistItem,
    Playlist,
    PlaylistEntry,
)
from .playlist_link import PlaylistLink, SyncDirection, SyncStatus

# Preference entities
from .preference import (
    PREFERENCE_ORDER,
    PreferenceEvent,
    PreferenceState,
    TrackPreference,
)

# Shared utilities
from .shared import MetricValue, ensure_utc, utc_now_factory

# Sourced metadata (shared by preferences, tags, playlist metadata)
from .sourced_metadata import SOURCE_PRIORITY, MetadataSource, should_override

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

# Workflow definition entities
from .workflow import WorkflowDef, WorkflowTaskDef

__all__ = [
    "DB_PSEUDO_CONNECTOR",
    "PREFERENCE_ORDER",
    "SOURCE_PRIORITY",
    "Artist",
    "CheckStatus",
    "ConnectorPlaylist",
    "ConnectorPlaylistItem",
    "ConnectorTrack",
    "ConnectorTrackMapping",
    "ConnectorTrackPlay",
    "IntegrityCheckResult",
    "IntegrityReport",
    "MetadataKey",
    "MetadataSource",
    "MetricValue",
    "OperationResult",
    "PlayRecord",
    "Playlist",
    "PlaylistEntry",
    "PlaylistLink",
    "PreferenceEvent",
    "PreferenceState",
    "SummaryMetric",
    "SummaryMetricCollection",
    "SyncCheckpoint",
    "SyncCheckpointStatus",
    "SyncDirection",
    "SyncStatus",
    "Track",
    "TrackLike",
    "TrackList",
    "TrackListMetadata",
    "TrackMapping",
    "TrackMetric",
    "TrackPlay",
    "TrackPreference",
    "WorkflowDef",
    "WorkflowTaskDef",
    "create_lastfm_play_record",
    "ensure_utc",
    "should_override",
    "utc_now_factory",
]
