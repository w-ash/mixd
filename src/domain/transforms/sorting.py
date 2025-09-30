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

from toolz import curry

from src.domain.entities.track import Track, TrackList

# Type alias for transformation functions
Transform = Callable[[TrackList], TrackList]


@curry
def sort_by_key_function(
    key_fn: Callable[[Track], Any],
    reverse: bool = False,
    metric_name: str | None = None,
    tracklist: TrackList | None = None,
) -> Transform | TrackList:
    """Pure sorting function - sorts tracks by the provided key function.

    Simple domain function that does one thing: sort tracks using the key function.
    Optionally tracks the sort values in tracklist metadata for downstream use.

    Args:
        key_fn: Function to extract sort key from each track
        reverse: Whether to sort in descending order
        metric_name: Optional name to store sort values in metadata
        tracklist: Optional tracklist to transform immediately

    Returns:
        Transformation function or transformed tracklist if provided
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

    return transform(tracklist) if tracklist is not None else transform