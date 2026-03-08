"""Unit tests for GetDashboardStatsUseCase.

Tests the dashboard aggregation logic with mocked repositories,
verifying correct delegation and result construction.
"""

import pytest

from src.application.use_cases.get_dashboard_stats import (
    GetDashboardStatsCommand,
    GetDashboardStatsUseCase,
)
from tests.fixtures import make_mock_uow


@pytest.fixture
def mock_uow():
    return make_mock_uow()


class TestGetDashboardStatsUseCase:
    """GetDashboardStatsUseCase collects counts from all repositories."""

    async def test_returns_all_stats(self, mock_uow):
        """Happy path: all repos return known values."""
        mock_uow.get_track_repository().count_all_tracks.return_value = 150
        mock_uow.get_plays_repository().count_all_plays.return_value = 4200
        mock_uow.get_playlist_repository().count_all_playlists.return_value = 7
        mock_uow.get_like_repository().count_total_liked.return_value = 85
        mock_uow.get_connector_repository().count_tracks_by_connector.return_value = {
            "spotify": 120,
            "lastfm": 90,
        }
        mock_uow.get_like_repository().count_liked_by_service.return_value = {
            "spotify": 42,
            "lastfm": 30,
        }

        result = await GetDashboardStatsUseCase().execute(
            GetDashboardStatsCommand(), mock_uow
        )

        assert result.total_tracks == 150
        assert result.total_plays == 4200
        assert result.total_playlists == 7
        assert result.total_liked == 85
        assert result.tracks_by_connector == {"spotify": 120, "lastfm": 90}
        assert result.liked_by_connector == {"spotify": 42, "lastfm": 30}

    async def test_empty_database(self, mock_uow):
        """All counts zero, empty dicts for an empty database."""
        mock_uow.get_track_repository().count_all_tracks.return_value = 0
        mock_uow.get_plays_repository().count_all_plays.return_value = 0
        mock_uow.get_playlist_repository().count_all_playlists.return_value = 0
        mock_uow.get_like_repository().count_total_liked.return_value = 0
        mock_uow.get_connector_repository().count_tracks_by_connector.return_value = {}
        mock_uow.get_like_repository().count_liked_by_service.return_value = {}

        result = await GetDashboardStatsUseCase().execute(
            GetDashboardStatsCommand(), mock_uow
        )

        assert result.total_tracks == 0
        assert result.total_plays == 0
        assert result.total_playlists == 0
        assert result.total_liked == 0
        assert result.tracks_by_connector == {}
        assert result.liked_by_connector == {}

    async def test_liked_by_service_uses_batch_query(self, mock_uow):
        """count_liked_by_service is called once (not N+1 per connector)."""
        mock_uow.get_track_repository().count_all_tracks.return_value = 50
        mock_uow.get_plays_repository().count_all_plays.return_value = 100
        mock_uow.get_playlist_repository().count_all_playlists.return_value = 2
        mock_uow.get_like_repository().count_total_liked.return_value = 10
        mock_uow.get_connector_repository().count_tracks_by_connector.return_value = {
            "spotify": 30,
            "lastfm": 20,
            "musicbrainz": 10,
        }
        mock_uow.get_like_repository().count_liked_by_service.return_value = {
            "spotify": 5,
            "musicbrainz": 3,
        }

        result = await GetDashboardStatsUseCase().execute(
            GetDashboardStatsCommand(), mock_uow
        )

        like_repo = mock_uow.get_like_repository()
        like_repo.count_liked_by_service.assert_called_once()
        assert result.liked_by_connector == {"spotify": 5, "musicbrainz": 3}
