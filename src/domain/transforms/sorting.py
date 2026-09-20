"""Pure functional sorting transformations for track collections.

This module contains immutable, side-effect free sorting functions that operate
solely on Track and TrackList domain entities. These are pure domain transforms
with zero external dependencies.

All sorting functions follow functional programming principles:
- Immutability: Return new TrackList instead of modifying existing ones
- Composition: Can be combined with other transforms via pipeline composition
- Dual-mode: Transform factories can execute immediately or return composable functions
- Purity: No side effects, logging, or external dependencies
"""

from collections.abc import Callable
from typing import cast
from uuid import UUID

from src.domain.entities.shared import MetricValue, SortKey
from src.domain.entities.track import Track, TrackList
from src.domain.transforms.core import Transform, dual_mode


def sort_by_key_function(
    key_fn: Callable[[Track], SortKey],
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

    Returns:
        Transformation function
    """

    def transform(t: TrackList) -> TrackList:
        """Apply the sorting transformation."""
        sorted_tracks = sorted(t.tracks, key=key_fn, reverse=reverse)
        result = t.with_tracks(sorted_tracks)

        # Optionally track sort values in metadata for downstream consumers.
        # Stored under the "metrics" key, but SortKey includes `str` (title sort)
        # which isn't a MetricValue — consumers filter via isinstance(v, (int, float)).
        if metric_name:
            track_metrics = cast(
                dict[UUID, MetricValue],
                {track.id: key_fn(track) for track in t.tracks},
            )
            existing_metrics = result.metadata.get("metrics", {})
            merged: dict[str, dict[UUID, MetricValue]] = {
                **existing_metrics,
                metric_name: track_metrics,
            }
            result = result.with_metadata("metrics", merged)

        return result

    return dual_mode(transform, tracklist)


def sort_by_artist_name(
    reverse: bool = False,
    tracklist: TrackList | None = None,
) -> Transform | TrackList:
    """Sort tracks alphabetically by the primary artist's credited name.

    Comparison is casefolded for locale-naive case-insensitive ordering.
    Tracks with no artist credits sort last regardless of direction — they
    are partitioned out before sorting rather than folded into the sort key,
    so ``reverse`` never pulls them back to the front.

    Args:
        reverse: Whether to sort in descending order.

    Returns:
        Transformation function
    """

    def credited_name(track: Track) -> str:
        return track.artists[0].credited_name.casefold()

    def transform(t: TrackList) -> TrackList:
        with_credit = [track for track in t.tracks if track.artists]
        without_credit = [track for track in t.tracks if not track.artists]
        sorted_tracks = sorted(with_credit, key=credited_name, reverse=reverse)
        return t.with_tracks(sorted_tracks + without_credit)

    return dual_mode(transform, tracklist)
