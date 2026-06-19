"""Track-metric repository protocol.

Split from the former monolithic ``interfaces.py``.
"""

from collections.abc import Awaitable
from typing import Protocol
from uuid import UUID

from src.domain.entities import (
    TrackMetric,
)


class MetricsRepositoryProtocol(Protocol):
    """Repository interface for track metrics operations."""

    def save_track_metrics(
        self,
        metrics: list[TrackMetric],
    ) -> Awaitable[int]:
        """Save metrics for multiple tracks efficiently.

        Args:
            metrics: List of ``TrackMetric`` entities — bool-valued metrics
                must be coerced to ``float`` at the construction boundary
                (the DB column is ``float``).

        Returns:
            Number of metrics saved
        """
        ...

    def get_track_metrics(
        self,
        track_ids: list[UUID],
        metric_type: str = "play_count",
        connector: str = "lastfm",
        max_age_hours: float = 24.0,
    ) -> Awaitable[dict[UUID, float]]:
        """Get cached metrics with TTL awareness.

        Args:
            track_ids: List of track IDs to get metrics for
            metric_type: Type of metric to retrieve
            connector: Connector that provided the metrics
            max_age_hours: Maximum age of metrics to accept (in hours)

        Returns:
            Dictionary mapping track IDs to their metric values
        """
        ...
