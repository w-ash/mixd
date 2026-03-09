"""Metric-based transformations for track collections.

This module contains transformations that operate on tracks using external metrics
stored in TrackList metadata. These transforms coordinate between domain entities
and application-layer metric enrichment.

Unlike pure domain transforms, these functions:
- Access metadata structures (metadata["metrics"][metric_name])
- Use logging for debugging
- Depend on external metric enrichment having occurred first
"""

# pyright: reportExplicitAny=false, reportAny=false
# Legitimate Any: use case results, OperationResult metadata, metric values

from datetime import UTC, datetime
from typing import Any, cast

from src.config import get_logger
from src.domain.entities.track import Track, TrackList
from src.domain.transforms.core import Transform
from src.domain.transforms.filtering import filter_by_predicate

from ._helpers import parse_datetime_safe

logger = get_logger(__name__)


def _warn_missing_metrics(operation: str, metric_name: str, tracklist: TrackList) -> None:
    """Log a warning when a metric operation has no metric data."""
    if tracklist.tracks:
        logger.warning(
            f"{operation} '{metric_name}' has no metric data — "
            "ensure an upstream enricher for this metric is configured"
        )


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

    def transform(t: TrackList) -> TrackList:
        """Apply the metric filter transformation."""
        metrics = t.metadata.get("metrics", {})
        metric_values: dict[int, Any] = metrics.get(metric_name, {})

        if not metric_values:
            _warn_missing_metrics("Filter by", metric_name, t)

        def is_in_range(track: Track) -> bool:
            """Check if track's metric is within the specified range."""
            if not track.id:
                return include_missing

            if track.id not in metric_values:
                return include_missing

            value: Any = metric_values[track.id]

            if min_value is not None and value < min_value:
                return False

            return not (max_value is not None and value > max_value)

        filter_func = cast(Transform, filter_by_predicate(is_in_range))
        result = filter_func(t)

        logger.debug(
            "Metric range filter applied",
            metric_name=metric_name,
            min_value=min_value,
            max_value=max_value,
            include_missing=include_missing,
            original_count=len(t.tracks),
            filtered_count=len(result.tracks),
            removed_count=len(t.tracks) - len(result.tracks),
        )

        return result

    return transform(tracklist) if tracklist is not None else transform


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

        if not metrics_dict:
            _warn_missing_metrics("Sort by", metric_name, t)

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


# Mapping from date_source parameter to metadata metric key
_DATE_SOURCE_METRIC_KEYS = {
    "first_played": "first_played_dates",
    "last_played": "last_played_dates",
}


def sort_by_date(
    date_source: str,
    ascending: bool = True,
    tracklist: TrackList | None = None,
) -> Transform | TrackList:
    """Sort tracks by a date value from metadata.

    Handles three date sources with consistent null-handling and type coercion:
    - "added_at": When the track was added to its source playlist
    - "first_played": When the track was first played (requires play history enrichment)
    - "last_played": When the track was most recently played (requires play history enrichment)

    Args:
        date_source: One of "added_at", "first_played", "last_played"
        ascending: If True, oldest first; if False, newest first
        tracklist: Optional tracklist to transform immediately

    Returns:
        Transformation function or transformed tracklist if provided
    """

    def transform(t: TrackList) -> TrackList:
        if date_source == "added_at":
            date_map = t.metadata.get("added_at_dates", {})
        else:
            metric_key = _DATE_SOURCE_METRIC_KEYS[date_source]
            date_map = t.metadata.get("metrics", {}).get(metric_key, {})

        # Tracks without dates sort to the end regardless of direction
        sentinel = (
            datetime.max.replace(tzinfo=UTC)
            if ascending
            else datetime.min.replace(tzinfo=UTC)
        )

        def date_key(track: Track) -> datetime:
            if not track.id or track.id not in date_map:
                return sentinel
            value = date_map[track.id]
            if isinstance(value, datetime):
                return value if value.tzinfo else value.replace(tzinfo=UTC)
            if isinstance(value, str):
                return parse_datetime_safe(value) or sentinel
            return sentinel

        sorted_tracks = sorted(t.tracks, key=date_key, reverse=not ascending)

        logger.debug(
            "Date sort applied",
            date_source=date_source,
            ascending=ascending,
            track_count=len(sorted_tracks),
            tracks_with_dates=sum(
                1 for track in t.tracks if track.id and track.id in date_map
            ),
        )

        return t.with_tracks(sorted_tracks)

    return transform(tracklist) if tracklist is not None else transform


def filter_by_explicit(
    keep: str = "all",
    tracklist: TrackList | None = None,
) -> Transform | TrackList:
    """Filter tracks by explicit content flag.

    Requires upstream Spotify enrichment to populate the explicit_flag metric.

    Args:
        keep: Which tracks to keep - "explicit", "clean", or "all" (no-op)
        tracklist: Optional tracklist to transform immediately

    Returns:
        Transformation function or transformed tracklist if provided
    """

    def transform(t: TrackList) -> TrackList:
        if keep == "all":
            return t

        metrics = t.metadata.get("metrics", {}).get("explicit_flag", {})
        want_explicit = keep == "explicit"

        def matches(track: Track) -> bool:
            if not track.id or track.id not in metrics:
                return not want_explicit  # Missing data = assume clean
            return bool(metrics[track.id]) == want_explicit

        result = cast(Transform, filter_by_predicate(matches))(t)

        logger.debug(
            "Explicit filter applied",
            keep=keep,
            original_count=len(t.tracks),
            filtered_count=len(result.tracks),
        )

        return result

    return transform(tracklist) if tracklist is not None else transform
