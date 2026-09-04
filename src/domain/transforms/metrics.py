"""Metric-based transformations for track collections.

Transforms tracks using metrics stored in ``TrackList.metadata["metrics"]``.
Enrichment happens upstream — these functions only read what is already on the
entity, so a metric that was never enriched degrades gracefully rather than
raising.

Purity: No side effects, logging, or external dependencies.
"""

from datetime import UTC, datetime
from operator import itemgetter
from typing import cast
from uuid import UUID

from src.domain.entities.shared import MetricValue, SortKey
from src.domain.entities.track import Track, TrackList
from src.domain.transforms.core import Transform, dual_mode
from src.domain.transforms.filtering import filter_by_predicate

from ._metadata_helpers import DATE_SOURCE_METRIC_KEYS, parse_datetime_safe


def has_metric_values(tracklist: TrackList, metric_name: str) -> bool:
    """Report whether the tracklist carries any value for ``metric_name``.

    Callers use this to detect a missing upstream enricher before applying a
    metric transform — the transforms themselves degrade gracefully.
    """
    return bool(tracklist.metadata.get("metrics", {}).get(metric_name, {}))


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
        metric_values: dict[UUID, MetricValue] = metrics.get(metric_name, {})

        def is_in_range(track: Track) -> bool:
            """Check if track's metric is within the specified range."""
            if not track.id:
                return include_missing

            if track.id not in metric_values:
                return include_missing

            value = metric_values[track.id]
            if not isinstance(value, (int, float)):
                return include_missing

            if min_value is not None and value < min_value:
                return False

            return not (max_value is not None and value > max_value)

        filter_func = cast(Transform, filter_by_predicate(is_in_range))
        return filter_func(t)

    return dual_mode(transform, tracklist)


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
        metrics_dict = t.metadata.get("metrics", {}).get(metric_name, {})

        def value_of(track: Track) -> SortKey | None:
            """Metric value for the track, or None when unenriched."""
            if not track.id:
                return None
            return metrics_dict.get(track.id)

        # Sort only the tracks that carry a value, so values of one metric
        # type are never compared against a sentinel of another. Tracks
        # without a value keep their order at the end in both directions.
        present = [
            (value, track)
            for track in t.tracks
            if (value := value_of(track)) is not None
        ]
        missing = [track for track in t.tracks if value_of(track) is None]
        present.sort(key=itemgetter(0), reverse=reverse)
        return t.with_tracks([track for _, track in present] + missing)

    return dual_mode(transform, tracklist)


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
            metric_key = DATE_SOURCE_METRIC_KEYS[date_source]
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
        return t.with_tracks(sorted_tracks)

    return dual_mode(transform, tracklist)


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

        return cast(Transform, filter_by_predicate(matches))(t)

    return dual_mode(transform, tracklist)
