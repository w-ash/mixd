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
from src.domain.repositories.stats import DashboardAggregates
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
                total_favorite_artists=9,
                tracks_by_connector={"spotify": 120, "lastfm": 90},
                liked_by_connector={"spotify": 42, "lastfm": 30},
                plays_by_connector={"spotify": 3000, "lastfm": 1200},
                playlists_by_connector={"spotify": 5, "lastfm": 2},
                preference_counts={"star": 10, "yah": 20, "hmm": 5, "nah": 3},
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
        assert result.total_favorite_artists == 9
        assert result.preference_counts == {"star": 10, "yah": 20, "hmm": 5, "nah": 3}
        # One aggregate query, scoped to the requesting user.
        mock_uow.get_stats_repository().get_dashboard_aggregates.assert_awaited_once_with(
            user_id="test-user"
        )
