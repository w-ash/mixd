"""Application-layer track transformations with metadata dependencies.

This package contains transformations that depend on external metadata enrichment,
logging, and application-layer coordination. Unlike pure domain transforms, these
can access TrackList metadata structures and external services.

Modules:
- play_history: Filter and sort by play counts and listening dates
- metrics: Filter and sort by external metrics (Last.fm, Spotify popularity, etc.)
- shuffle: Weighted shuffle blending original and random orderings
"""

from .metrics import filter_by_metric_range, sort_by_external_metrics
from .play_history import (
    filter_by_play_history,
    filter_by_time_criteria,
    sort_by_play_history,
    time_range_predicate,
)
from .shuffle import weighted_shuffle

__all__ = [
    # Metric transforms
    "filter_by_metric_range",
    # Play history transforms
    "filter_by_play_history",
    "filter_by_time_criteria",
    "sort_by_external_metrics",
    "sort_by_play_history",
    "time_range_predicate",
    # Shuffle transforms
    "weighted_shuffle",
]