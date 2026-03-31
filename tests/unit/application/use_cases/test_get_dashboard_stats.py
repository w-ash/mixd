"""Unit tests for GetDashboardStatsUseCase.

Tests the dashboard aggregation logic with a mocked stats repository,
verifying correct delegation and result construction from the
single-query aggregation pattern.
"""

import pytest

from src.application.use_cases.get_dashboard_stats import (
    GetDashboardStatsCommand,
    GetDashboardStatsUseCase,
)
from src.domain.repositories.interfaces import DashboardAggregates
from tests.fixtures import make_mock_uow


@pytest.fixture
def mock_uow():
    return make_mock_uow()


class TestGetDashboardStatsUseCase:
    """GetDashboardStatsUseCase delegates to StatsRepository."""

    async def test_returns_all_stats(self, mock_uow):
        """Happy path: stats repo returns known aggregates."""
        mock_uow.get_stats_repository().get_dashboard_aggregates.return_value = (
            DashboardAggregates(
                total_tracks=150,
                total_plays=4200,
                total_playlists=7,
                total_liked=85,
                tracks_by_connector={"spotify": 120, "lastfm": 90},
                liked_by_connector={"spotify": 42, "lastfm": 30},
                plays_by_connector={"spotify": 3000, "lastfm": 1200},
                playlists_by_connector={"spotify": 5, "lastfm": 2},
            )
        )

        result = await GetDashboardStatsUseCase().execute(
            GetDashboardStatsCommand(user_id="test-user"), mock_uow
        )

        assert result.total_tracks == 150
        assert result.total_plays == 4200
        assert result.total_playlists == 7
        assert result.total_liked == 85
        assert result.tracks_by_connector == {"spotify": 120, "lastfm": 90}
        assert result.liked_by_connector == {"spotify": 42, "lastfm": 30}
        assert result.plays_by_connector == {"spotify": 3000, "lastfm": 1200}
        assert result.playlists_by_connector == {"spotify": 5, "lastfm": 2}

    async def test_empty_database(self, mock_uow):
        """All counts zero, empty dicts for an empty database."""
        mock_uow.get_stats_repository().get_dashboard_aggregates.return_value = (
            DashboardAggregates(
                total_tracks=0,
                total_plays=0,
                total_playlists=0,
                total_liked=0,
                tracks_by_connector={},
                liked_by_connector={},
                plays_by_connector={},
                playlists_by_connector={},
            )
        )

        result = await GetDashboardStatsUseCase().execute(
            GetDashboardStatsCommand(user_id="test-user"), mock_uow
        )

        assert result.total_tracks == 0
        assert result.total_plays == 0
        assert result.total_playlists == 0
        assert result.total_liked == 0
        assert result.tracks_by_connector == {}
        assert result.liked_by_connector == {}

    async def test_single_repo_call(self, mock_uow):
        """Stats repo is called exactly once (not 8 separate queries)."""
        mock_uow.get_stats_repository().get_dashboard_aggregates.return_value = (
            DashboardAggregates(
                total_tracks=50,
                total_plays=100,
                total_playlists=2,
                total_liked=10,
                tracks_by_connector={"spotify": 30},
                liked_by_connector={"spotify": 5},
                plays_by_connector={"spotify": 80},
                playlists_by_connector={"spotify": 1},
            )
        )

        await GetDashboardStatsUseCase().execute(
            GetDashboardStatsCommand(user_id="test-user"), mock_uow
        )

        stats_repo = mock_uow.get_stats_repository()
        stats_repo.get_dashboard_aggregates.assert_called_once()
