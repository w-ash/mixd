"""Use case for retrieving aggregate dashboard statistics.

Collects counts from multiple repositories in a single UoW transaction
to present a summary of the user's music library on the dashboard.
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
            track_repo = uow.get_track_repository()
            plays_repo = uow.get_plays_repository()
            playlist_repo = uow.get_playlist_repository()
            like_repo = uow.get_like_repository()
            connector_repo = uow.get_connector_repository()
            link_repo = uow.get_playlist_link_repository()

            # Sequential: overhead of TaskGroup not justified for a few small queries
            total_tracks = await track_repo.count_all_tracks()
            total_plays = await plays_repo.count_all_plays()
            total_playlists = await playlist_repo.count_all_playlists()
            total_liked = await like_repo.count_total_liked()
            tracks_by_connector = await connector_repo.count_tracks_by_connector()
            liked_by_connector = await like_repo.count_liked_by_service()
            plays_by_connector = await plays_repo.count_plays_by_service()
            playlists_by_connector = await link_repo.count_links_by_connector()

            return DashboardStatsResult(
                total_tracks=total_tracks,
                total_plays=total_plays,
                total_playlists=total_playlists,
                total_liked=total_liked,
                tracks_by_connector=tracks_by_connector,
                liked_by_connector=liked_by_connector,
                plays_by_connector=plays_by_connector,
                playlists_by_connector=playlists_by_connector,
            )
