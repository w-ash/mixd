"""Pure functional selection transformations for track collections.

This module contains immutable, side-effect free selection functions that operate
solely on Track and TrackList domain entities. These are pure domain transforms
with zero external dependencies.

All selection functions follow functional programming principles:
- Immutability: Return new TrackList instead of modifying existing ones
- Composition: Can be combined with other transforms via pipeline composition
- Dual-mode: Transform factories can execute immediately or return composable functions
- Purity: No side effects, logging, or external dependencies
"""

import random
from typing import cast

from src.domain.entities.track import TrackList
from src.domain.transforms.core import Transform, optional_tracklist_transform


@optional_tracklist_transform
def limit(count: int) -> Transform:
    """
    Limit to the first n tracks.

    Args:
        count: Maximum number of tracks to keep

    Returns:
        Transformation function
    """

    def transform(t: TrackList) -> TrackList:
        return t.with_tracks(t.tracks[:count])

    return transform


@optional_tracklist_transform
def take_last(count: int) -> Transform:
    """
    Take the last n tracks.

    Args:
        count: Number of tracks to keep from the end

    Returns:
        Transformation function
    """

    def transform(t: TrackList) -> TrackList:
        n = min(count, len(t.tracks))
        return t.with_tracks(t.tracks[-n:])

    return transform


@optional_tracklist_transform
def sample_random(count: int) -> Transform:
    """
    Randomly sample n tracks.

    Args:
        count: Number of tracks to sample

    Returns:
        Transformation function
    """

    def transform(t: TrackList) -> TrackList:
        n = min(count, len(t.tracks))
        selected = random.sample(t.tracks, n)
        return t.with_tracks(selected)

    return transform


@optional_tracklist_transform
def select_by_method(count: int, method: str = "first") -> Transform:
    """
    Select tracks using specified method.

    Args:
        count: Number of tracks to select
        method: Selection method ("first", "last", or "random")

    Returns:
        Transformation function
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
        return cast(Transform, transform_fn)(t)

    return transform


@optional_tracklist_transform
def reverse_tracks() -> Transform:
    """
    Reverse the order of tracks.

    Returns:
        Transformation function
    """

    def transform(t: TrackList) -> TrackList:
        return t.with_tracks(list(reversed(t.tracks)))

    return transform


@optional_tracklist_transform
def select_by_percentage(
    percentage: float,
    method: str = "first",
) -> Transform:
    """
    Select a percentage of tracks.

    Args:
        percentage: Percentage of tracks to select (0-100)
        method: Selection method ("first", "last", or "random")

    Returns:
        Transformation function
    """

    def transform(t: TrackList) -> TrackList:
        count = max(1, round(len(t.tracks) * percentage / 100))
        return cast(TrackList, select_by_method(count, method, tracklist=t))

    return transform
