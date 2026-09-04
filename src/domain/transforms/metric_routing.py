"""Metric classification and sort routing for workflow transforms.

Classifies a metric by its data source (track attribute, play history, external)
and routes metric-based sorting to the matching transform.

Purity: No side effects, logging, or external dependencies.
"""

from collections.abc import Callable
from datetime import UTC, datetime

from src.domain.entities.shared import SortKey
from src.domain.entities.track import Track, TrackList
from src.domain.transforms.core import Transform
from src.domain.transforms.metrics import sort_by_external_metrics
from src.domain.transforms.play_history import sort_by_play_history
from src.domain.transforms.sorting import sort_by_key_function

# Track entity fields usable as sort keys
TRACK_ATTRIBUTES = {"title", "album", "release_date", "duration_ms", "artist"}

# Internal play history DB aggregates
PLAY_HISTORY_METRICS = {
    "total_plays",
    "plays_last_7_days",
    "plays_last_30_days",
    "plays_last_90_days",
}


def classify_metric(metric_name: str) -> str:
    """Classify metric by data source for transform routing.

    Uses open-ended classification: anything not explicitly a track attribute
    or play history metric is treated as an external connector metric.
    Specific metric validation happens at the enrichment boundary
    (node_factories.py validates against the connector registry).
    """
    if metric_name in TRACK_ATTRIBUTES:
        return "track_attribute"
    if metric_name in PLAY_HISTORY_METRICS:
        return "play_history"
    return "external_metric"


def resolve_sort_key_function(value_name: str) -> Callable[[Track], SortKey] | None:
    """Resolve value name to appropriate key function for track attributes.

    Args:
        value_name: Name of track attribute to sort by

    Returns:
        Key function for extracting the attribute from Track entities
    """
    track_attribute_extractors: dict[str, Callable[[Track], SortKey]] = {
        "title": lambda track: track.title,
        "album": lambda track: track.album or "",
        "release_date": lambda track: (
            track.release_date or datetime.min.replace(tzinfo=UTC)
        ),
        "duration_ms": lambda track: track.duration_ms or 0,
        "artist": lambda track: track.artists[0].name if track.artists else "",
    }

    return track_attribute_extractors.get(value_name)


def route_metric_sorting(metric_name: str, *, reverse: bool) -> Transform | TrackList:
    """Route metric sorting to the transform matching the metric's data source."""
    category = classify_metric(metric_name)

    if category == "track_attribute":
        key_fn = resolve_sort_key_function(metric_name)
        if key_fn is None:
            raise ValueError(f"Unknown track attribute: {metric_name}")
        return sort_by_key_function(
            key_fn=key_fn,
            reverse=reverse,
            metric_name=metric_name,
        )

    if category == "play_history":
        return sort_by_play_history(reverse=reverse)

    # External or unrecognized metrics route to external metric sorting.
    # If no upstream enricher populated the metric, sort is a graceful no-op
    # (tracks without metrics sort to end).
    return sort_by_external_metrics(
        metric_name=metric_name,
        reverse=reverse,
    )
