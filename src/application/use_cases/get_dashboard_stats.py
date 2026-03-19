"""Use case for retrieving aggregate dashboard statistics.

Delegates to a single StatsRepository that computes all counts in
minimal round trips, instead of querying 6 repositories sequentially.
"""

from attrs import define

from src.domain.repositories.interfaces import UnitOfWorkProtocol


@define(frozen=True, slots=True)
class GetDashboardStatsCommand:
    """Parameterless — exists for API uniformity."""


@define(frozen=True, slots=True)
class DashboardStatsResult:
    """Aggregate statistics for the dashboard."""

    total_tracks: int
    total_plays: int
    total_playlists: int
    total_liked: int
    tracks_by_connector: dict[str, int]
    liked_by_connector: dict[str, int]
    plays_by_connector: dict[str, int]
    playlists_by_connector: dict[str, int]


@define(slots=True)
class GetDashboardStatsUseCase:
    """Use case for retrieving dashboard statistics."""

    async def execute(
        self, command: GetDashboardStatsCommand, uow: UnitOfWorkProtocol
    ) -> DashboardStatsResult:
        """Execute the dashboard stats aggregation.

        Args:
            command: Parameterless command for API uniformity.
            uow: Unit of work for repository access.

        Returns:
            DashboardStatsResult containing all aggregate counts.
        """
        async with uow:
            stats = await uow.get_stats_repository().get_dashboard_aggregates()

            return DashboardStatsResult(**stats)
