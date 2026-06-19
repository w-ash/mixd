"""Dashboard / stats repository protocol.

Split from the former monolithic ``interfaces.py``.
"""

from collections.abc import Awaitable
from typing import Protocol, TypedDict

from src.domain.entities.preference import (
    PreferenceState,
)


class DashboardAggregates(TypedDict):
    """Result shape for the single-query dashboard stats aggregation."""

    total_tracks: int
    total_plays: int
    total_playlists: int
    total_liked: int
    tracks_by_connector: dict[str, int]
    liked_by_connector: dict[str, int]
    plays_by_connector: dict[str, int]
    playlists_by_connector: dict[str, int]
    preference_counts: dict[PreferenceState, int]


class StatsRepositoryProtocol(Protocol):
    """Cross-table read-only aggregation queries."""

    def get_dashboard_aggregates(
        self, *, user_id: str
    ) -> Awaitable[DashboardAggregates]:
        """Compute all dashboard counts in minimal round trips."""
        ...
