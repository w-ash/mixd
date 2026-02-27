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
from .core import Transform, create_pipeline, optional_tracklist_transform
from .filtering import (
    exclude_artists,
    exclude_tracks,
    filter_by_date_range,
    filter_by_duration,
    filter_by_liked_status,
    filter_by_predicate,
    filter_duplicates,
)
from .playlist_operations import (
    calculate_track_list_diff,
    rename,
    reorder_to_match_target,
    set_description,
)
from .selecting import (
    limit,
    reverse_tracks,
    sample_random,
    select_by_method,
    select_by_percentage,
    take_last,
)
from .sorting import sort_by_key_function

__all__ = [
    # Core pipeline functions
    "Transform",
    # Playlist operations
    "calculate_track_list_diff",
    # Track combination
    "concatenate",
    "create_pipeline",
    # Track filtering
    "exclude_artists",
    "exclude_tracks",
    "filter_by_date_range",
    "filter_by_duration",
    "filter_by_liked_status",
    "filter_by_predicate",
    "filter_duplicates",
    "interleave",
    "intersect",
    # Track selection
    "limit",
    "optional_tracklist_transform",
    "rename",
    "reorder_to_match_target",
    "reverse_tracks",
    "sample_random",
    "select_by_method",
    "select_by_percentage",
    "set_description",
    # Track sorting
    "sort_by_key_function",
    "take_last",
]
