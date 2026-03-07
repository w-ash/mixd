"""Metadata-aware track transformations.

Unlike pure domain transforms (src/domain/transforms/), these functions
depend on upstream enrichment data in TrackList.metadata — play counts,
play counts, explicit flags, and listening dates. They also use
logging for diagnostic output.

If your transform only needs Track fields (title, artists, duration, etc.),
it belongs in src/domain/transforms/ instead.
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
