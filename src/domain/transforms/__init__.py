"""Pure functional transformations for domain entities.

This package contains immutable, side-effect free transformations that operate
solely on Track, TrackList, and Playlist domain entities. All functions are pure
with zero external dependencies (no logging, no config, no metadata assumptions).

Modules:
- core: Pipeline composition and Transform type alias
- filtering: Pure track filtering operations
- sorting: Pure track sorting operations
- selecting: Pure track selection operations
- combining: Pure track combination operations
- playlist_operations: Pure playlist transformation operations
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
    filter_duplicates,
)
from .playlist_operations import reorder_to_match_target
from .selecting import (
    reverse_tracks,
    select_by_method,
    select_by_percentage,
)
from .sorting import sort_by_key_function

__all__ = [
    "Transform",
    "concatenate",
    "exclude_artists",
    "exclude_tracks",
    "filter_by_date_range",
    "filter_by_duration",
    "filter_by_liked_status",
    "filter_by_predicate",
    "filter_duplicates",
    "interleave",
    "intersect",
    "reorder_to_match_target",
    "require_database_tracks",
    "reverse_tracks",
    "select_by_method",
    "select_by_percentage",
    "sort_by_key_function",
]
