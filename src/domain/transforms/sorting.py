"""Pure functional sorting transformations for track collections.

This module contains immutable, side-effect free sorting functions that operate
solely on Track and TrackList domain entities. These are pure domain transforms
with zero external dependencies.

All sorting functions follow functional programming principles:
- Immutability: Return new TrackList instead of modifying existing ones
- Composition: Can be combined with other transforms via create_pipeline
- Currying: Designed for partial application with toolz.curry
- Purity: No side effects, logging, or external dependencies
"""

from collections.abc import Callable
from typing import Any

from src.domain.entities.track import Track, TrackList
from src.domain.transforms.core import Transform, optional_tracklist_transform


@optional_tracklist_transform
def sort_by_key_function(
    key_fn: Callable[[Track], Any],
    reverse: bool = False,
    metric_name: str | None = None,
) -> Transform:
    """Pure sorting function - sorts tracks by the provided key function.

    Simple domain function that does one thing: sort tracks using the key function.
    Optionally tracks the sort values in tracklist metadata for downstream use.

    Args:
        key_fn: Function to extract sort key from each track
        reverse: Whether to sort in descending order
        metric_name: Optional name to store sort values in metadata

    Returns:
        Transformation function
    """

    def transform(t: TrackList) -> TrackList:
        """Apply the sorting transformation."""
        sorted_tracks = sorted(t.tracks, key=key_fn, reverse=reverse)
        result = t.with_tracks(sorted_tracks)

        # Optionally track sort values in metadata
        if metric_name:
            track_metrics = {
                track.id: key_fn(track) for track in t.tracks if track.id is not None
            }
            result = result.with_metadata(
                "metrics",
                {
                    **result.metadata.get("metrics", {}),
                    metric_name: track_metrics,
                },
            )

        return result

    return transform