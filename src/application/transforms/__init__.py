"""Application-layer track transformations with metadata dependencies.

This package contains transformations that depend on external metadata enrichment,
logging, and application-layer coordination. Unlike pure domain transforms, these
can access TrackList metadata structures and external services.

Modules:
- play_history: Filter and sort by play counts and listening dates
- metric_transforms: Filter and sort by external metrics (Last.fm, Spotify popularity, etc.)
- shuffle: Weighted shuffle blending original and random orderings
"""

from .metric_transforms import (
    filter_by_explicit,
    filter_by_metric_range,
    sort_by_date,
    sort_by_external_metrics,
)
from .play_history import (
    filter_by_play_history,
    sort_by_play_history,
)
from .shuffle import weighted_shuffle

__all__ = [
    "filter_by_explicit",
    "filter_by_metric_range",
    "filter_by_play_history",
    "sort_by_date",
    "sort_by_external_metrics",
    "sort_by_play_history",
    "weighted_shuffle",
]
