"""Pure functional selection transformations for track collections.

This module contains immutable, side-effect free selection functions that operate
solely on Track and TrackList domain entities. These are pure domain transforms
with zero external dependencies.

All selection functions follow functional programming principles:
- Immutability: Return new TrackList instead of modifying existing ones
- Composition: Can be combined with other transforms via create_pipeline
- Currying: Designed for partial application with toolz.curry
- Purity: No side effects, logging, or external dependencies
"""

from collections.abc import Callable
import random
from typing import cast

from toolz import curry

from src.domain.entities.track import TrackList

# Type alias for transformation functions
Transform = Callable[[TrackList], TrackList]


@curry
def limit(count: int, tracklist: TrackList | None = None) -> Transform | TrackList:
    """
    Limit to the first n tracks.

    Args:
        count: Maximum number of tracks to keep
        tracklist: Optional tracklist to transform immediately

    Returns:
        Transformation function or transformed tracklist if provided
    """

    def transform(t: TrackList) -> TrackList:
        return t.with_tracks(t.tracks[:count])

    return transform(tracklist) if tracklist is not None else transform


@curry
def take_last(
    count: int,
    tracklist: TrackList | None = None,
) -> Transform | TrackList:
    """
    Take the last n tracks.

    Args:
        count: Number of tracks to keep from the end
        tracklist: Optional tracklist to transform immediately

    Returns:
        Transformation function or transformed tracklist if provided
    """

    def transform(t: TrackList) -> TrackList:
        n = min(count, len(t.tracks))
        return t.with_tracks(t.tracks[-n:])

    return transform(tracklist) if tracklist is not None else transform


@curry
def sample_random(
    count: int,
    tracklist: TrackList | None = None,
) -> Transform | TrackList:
    """
    Randomly sample n tracks.

    Args:
        count: Number of tracks to sample
        tracklist: Optional tracklist to transform immediately

    Returns:
        Transformation function or transformed tracklist if provided
    """

    def transform(t: TrackList) -> TrackList:
        n = min(count, len(t.tracks))
        selected = random.sample(t.tracks, n)
        return t.with_tracks(selected)

    return transform(tracklist) if tracklist is not None else transform


@curry
def select_by_method(
    count: int,
    method: str = "first",
    tracklist: TrackList | None = None,
) -> Transform | TrackList:
    """
    Select tracks using specified method.

    Args:
        count: Number of tracks to select
        method: Selection method ("first", "last", or "random")
        tracklist: Optional tracklist to transform immediately

    Returns:
        Transformation function or transformed tracklist if provided
    """
    if method == "first":
        transform_fn = limit(count)
    elif method == "last":
        transform_fn = take_last(count)
    elif method == "random":
        transform_fn = sample_random(count)
    else:
        raise ValueError(f"Invalid selection method: {method}")

    def transform(t: TrackList) -> TrackList:
        result = cast("Transform", transform_fn)(t)
        return (
            cast("TrackList", result)
            .with_metadata("selection_method", method)
            .with_metadata("original_count", len(t.tracks))
        )

    return transform(tracklist) if tracklist is not None else transform