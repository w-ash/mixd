"""Pure functional transformations for domain entities.

This package contains immutable, side-effect free transformations that operate
on Track, TrackList, and Playlist domain entities. All functions are pure with
zero external dependencies (no logging, no config, no I/O).

Some transforms read enrichment data from ``TrackList.metadata`` (play counts,
tags, preferences, metrics). That is still pure — enrichment happens upstream
and these functions only read the entity they are given.

Modules:
- core: Pipeline composition and Transform type alias
- filtering: Pure track filtering operations
- sorting: Pure track sorting operations
- selecting: Pure track selection operations
- combining: Pure track combination operations
- playlist_operations: Pure playlist transformation operations
- metrics: Metric-driven filtering and sorting from metadata
- metric_routing: Metric classification and sort routing
- play_history: Play-count and listening-date filtering and sorting
- preference: Preference-state filtering and sorting
- shuffle: Weighted shuffle
- tag: Tag and tag-namespace filtering
"""

from .combining import concatenate, interleave, intersect
from .core import Transform, require_database_tracks
from .filtering import (
    exclude_artists,
    exclude_tracks,
    filter_by_date_range,
    filter_by_duration,
    filter_by_liked_status,
    filter_by_predicate,
    filter_by_release_year,
    filter_duplicates,
)
from .metrics import (
    filter_by_explicit,
    filter_by_metric_range,
    has_metric_values,
    sort_by_date,
    sort_by_external_metrics,
)
from .play_history import filter_by_play_history, sort_by_play_history
from .playlist_operations import reorder_to_match_target
from .preference import filter_by_preference, sort_by_preference
from .selecting import (
    reverse_tracks,
    select_by_method,
    select_by_percentage,
)
from .shuffle import weighted_shuffle
from .sorting import sort_by_key_function
from .tag import filter_by_tag, filter_by_tag_namespace

__all__ = [
    "Transform",
    "concatenate",
    "exclude_artists",
    "exclude_tracks",
    "filter_by_date_range",
    "filter_by_duration",
    "filter_by_explicit",
    "filter_by_liked_status",
    "filter_by_metric_range",
    "filter_by_play_history",
    "filter_by_predicate",
    "filter_by_preference",
    "filter_by_release_year",
    "filter_by_tag",
    "filter_by_tag_namespace",
    "filter_duplicates",
    "has_metric_values",
    "interleave",
    "intersect",
    "reorder_to_match_target",
    "require_database_tracks",
    "reverse_tracks",
    "select_by_method",
    "select_by_percentage",
    "sort_by_date",
    "sort_by_external_metrics",
    "sort_by_key_function",
    "sort_by_play_history",
    "sort_by_preference",
    "weighted_shuffle",
]
