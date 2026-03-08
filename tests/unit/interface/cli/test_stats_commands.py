"""Tests for CLI stats command."""

from unittest.mock import AsyncMock, patch

from typer.testing import CliRunner

from src.application.use_cases.get_dashboard_stats import DashboardStatsResult
from src.interface.cli.app import app

runner = CliRunner()


class TestStatsCommand:
    def test_shows_library_summary(self):
        mock_result = DashboardStatsResult(
            total_tracks=100,
            total_plays=5000,
            total_playlists=10,
            total_liked=50,
            tracks_by_connector={"spotify": 80, "lastfm": 20},
            liked_by_connector={"spotify": 50},
        )

        with patch(
            "src.application.runner.execute_use_case",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            result = runner.invoke(app, ["stats"])

            assert result.exit_code == 0
            assert "100" in result.output  # total tracks
            assert "5000" in result.output  # total plays
            assert "10" in result.output  # total playlists
            assert "50" in result.output  # total liked

    def test_shows_connector_breakdown(self):
        mock_result = DashboardStatsResult(
            total_tracks=100,
            total_plays=5000,
            total_playlists=10,
            total_liked=50,
            tracks_by_connector={"spotify": 80},
            liked_by_connector={},
        )

        with patch(
            "src.application.runner.execute_use_case",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            result = runner.invoke(app, ["stats"])

            assert result.exit_code == 0
            assert "spotify" in result.output
            assert "80" in result.output
