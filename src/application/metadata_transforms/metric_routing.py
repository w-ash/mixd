"""Metric classification and sort routing for workflow transforms.

Classifies metrics by data source (track attribute, play history, external)
and routes metric-based sorting to the appropriate domain function.
This is application-layer knowledge — the domain provides pure sort functions,
and this module makes the routing decisions.
"""

from collections.abc import Callable
from datetime import UTC, datetime

from src.application.metadata_transforms import (
    sort_by_external_metrics,
    sort_by_play_history,
)
from src.domain.entities.shared import SortKey
from src.domain.entities.track import Track, TrackList
from src.domain.transforms import sort_by_key_function
from src.domain.transforms.core import Transform

# Application-layer knowledge: Track entity fields usable as sort keys
TRACK_ATTRIBUTES = {"title", "album", "release_date", "duration_ms", "artist"}

# Application-layer knowledge: internal play history DB aggregates
PLAY_HISTORY_METRICS = {
    "total_plays",
    "plays_last_7_days",
    "plays_last_30_days",
    "plays_last_90_days",
    "last_played_date",
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


def route_metric_sorting(cfg: dict[str, object]) -> Transform | TrackList:
    """Route metric sorting to appropriate domain function based on data source.

    Clean separation of concerns: application layer makes routing decisions,
    domain layer provides pure functions for each data source type.
    """
    metric_name = cfg.get("metric_name")
    if not isinstance(metric_name, str):
        raise TypeError("metric_name must be a string for metric sorting")

    reverse: bool = bool(cfg.get("reverse", True))
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

    elif category == "play_history":
        return sort_by_play_history(reverse=reverse)

    else:
        # External or unrecognized metrics route to external metric sorting.
        # If no upstream enricher populated the metric, sort is a graceful no-op
        # (tracks without metrics sort to end).
        return sort_by_external_metrics(
            metric_name=metric_name,
            reverse=reverse,
        )
