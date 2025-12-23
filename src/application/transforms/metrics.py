"""Metric-based transformations for track collections.

This module contains transformations that operate on tracks using external metrics
stored in TrackList metadata. These transforms coordinate between domain entities
and application-layer metric enrichment.

Unlike pure domain transforms, these functions:
- Access metadata structures (metadata["metrics"][metric_name])
- Use logging for debugging
- Depend on external metric enrichment having occurred first
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from toolz import curry

from src.config import get_logger
from src.domain.entities.track import Track, TrackList
from src.domain.transforms.filtering import filter_by_predicate

logger = get_logger(__name__)

# Type alias for transformation functions
Transform = Callable[[TrackList], TrackList]


@curry
def filter_by_metric_range(
    metric_name: str,
    min_value: float | None = None,
    max_value: float | None = None,
    include_missing: bool = False,
    tracklist: TrackList | None = None,
) -> Transform | TrackList:
    """
    Filter tracks based on a metric value range.

    Args:
        metric_name: Name of the metric to filter by (e.g., 'lastfm_user_playcount')
        min_value: Minimum value (inclusive), or None for no minimum
        max_value: Maximum value (inclusive), or None for no maximum
        include_missing: Whether to include tracks without the metric
        tracklist: Optional tracklist to transform immediately

    Returns:
        Transformation function or transformed tracklist if provided
    """

    def is_in_range(track: Track) -> bool:
        """Check if track's metric is within the specified range."""
        if not track.id:
            return include_missing

        # Get the metrics dictionary from the tracklist metadata
        metrics = {} if tracklist is None else tracklist.metadata.get("metrics", {})
        metric_values = metrics.get(metric_name, {})

        # Check if track has the metric
        if track.id not in metric_values:
            return include_missing

        value = metric_values[track.id]

        # Check range bounds
        if min_value is not None and value < min_value:
            return False

        return not (max_value is not None and value > max_value)

    def transform(t: TrackList) -> TrackList:
        """Apply the metric filter transformation."""
        # Set the tracklist for metric lookup in is_in_range
        nonlocal tracklist
        tracklist = t

        # Apply filter
        filter_func = cast(Transform, filter_by_predicate(is_in_range))
        result = filter_func(t)

        # Add metadata about the filter operation
        filtered_count = len(result.tracks)
        return cast(TrackList, result).with_metadata(
            "filter_metrics",
            {
                "metric_name": metric_name,
                "min_value": min_value,
                "max_value": max_value,
                "include_missing": include_missing,
                "original_count": len(t.tracks),
                "filtered_count": filtered_count,
                "removed_count": len(t.tracks) - filtered_count,
            },
        )

    return transform(tracklist) if tracklist is not None else transform


@curry
def sort_by_external_metrics(
    metric_name: str,
    reverse: bool = True,
    tracklist: TrackList | None = None,
) -> Transform | TrackList:
    """Sort tracks by external metrics from tracklist metadata.

    Pure function that sorts tracks using metrics already resolved in tracklist metadata.
    Expects the application layer to have populated metadata["metrics"][metric_name]
    with the appropriate values.

    Args:
        metric_name: Name of metric in tracklist metadata
        reverse: Whether to sort in descending order (default True for metrics)
        tracklist: Optional tracklist to transform immediately

    Returns:
        Transformation function or transformed tracklist if provided
    """

    def transform(t: TrackList) -> TrackList:
        """Apply external metrics sorting."""
        # Get metrics from tracklist metadata
        metrics_dict = t.metadata.get("metrics", {}).get(metric_name, {})

        def external_metrics_key(track: Track) -> Any:
            """Extract metric value for sorting."""
            if not track.id or track.id not in metrics_dict:
                # Tracks without metrics sort to end (preserve original data types)
                if reverse:
                    return float("-inf")  # Lowest for descending sort
                else:
                    return float("inf")  # Highest for ascending sort

            return metrics_dict[track.id]

        sorted_tracks = sorted(t.tracks, key=external_metrics_key, reverse=reverse)
        result = t.with_tracks(sorted_tracks)

        # The metrics are already in metadata, no need to duplicate them
        return result

    return transform(tracklist) if tracklist is not None else transform