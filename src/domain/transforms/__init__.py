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

from .combining import concatenate, interleave
from .core import Transform, create_pipeline, optional_tracklist_transform
from .filtering import (
    exclude_artists,
    exclude_tracks,
    filter_by_date_range,
    filter_by_predicate,
    filter_duplicates,
)
from .playlist_operations import (
    calculate_track_list_diff,
    rename,
    reorder_to_match_target,
    set_description,
)
from .selecting import limit, sample_random, select_by_method, take_last
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
    "filter_by_predicate",
    "filter_duplicates",
    "interleave",
    # Track selection
    "limit",
    "optional_tracklist_transform",
    "rename",
    "reorder_to_match_target",
    "sample_random",
    "select_by_method",
    "set_description",
    # Track sorting
    "sort_by_key_function",
    "take_last",
]