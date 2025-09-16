"""Pure functional transformations for domain entities."""

from .core import (
    Transform,
    concatenate,
    create_pipeline,
    exclude_artists,
    exclude_tracks,
    filter_by_date_range,
    filter_by_metric_range,
    filter_by_predicate,
    filter_duplicates,
    interleave,
    limit,
    rename,
    sample_random,
    select_by_method,
    set_description,
    sort_by_external_metrics,
    sort_by_key_function,
    sort_by_play_history,
    take_last,
)

__all__ = [
    # Core pipeline functions
    "Transform",
    # Track list combination
    "concatenate",
    "create_pipeline",
    # Track filtering
    "exclude_artists",
    "exclude_tracks",
    "filter_by_date_range",
    "filter_by_metric_range",
    "filter_by_predicate",
    "filter_duplicates",
    "interleave",
    # Track selection
    "limit",
    # Playlist operations
    "rename",
    "sample_random",
    "select_by_method",
    "set_description",
    # Track sorting
    "sort_by_external_metrics",
    "sort_by_key_function",
    "sort_by_play_history",
    "take_last",
]
