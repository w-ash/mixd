"""Tests for CLI stats command."""

import re
from unittest.mock import AsyncMock, patch

from typer.testing import CliRunner

from src.application.use_cases.get_dashboard_stats import DashboardStatsResult
from src.interface.cli.app import app
from tests.fixtures import plain

runner = CliRunner()


class TestStatsCommand:
    def test_shows_library_summary(self):
        # Distinct totals so no value is a substring of another, and each is
        # read next to its own label.
        mock_result = DashboardStatsResult(
            total_tracks=1234,
            total_plays=98765,
            total_playlists=17,
            total_liked=321,
            total_favorite_artists=7,
            tracks_by_connector={"spotify": 80, "lastfm": 20},
            liked_by_connector={"spotify": 50},
            plays_by_connector={"spotify": 3000, "lastfm": 2000},
            playlists_by_connector={"spotify": 10},
            preference_counts={"star": 5, "yah": 12},
        )

        with patch(
            "src.application.runner.execute_use_case",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            result = runner.invoke(app, ["stats"])

        assert result.exit_code == 0
        output = plain(result.output)
        assert re.search(r"(?m)^\W*Tracks:\s*1234\b", output)
        assert re.search(r"Playlists:\s*17\b", output)
        assert re.search(r"Liked Tracks:\s*321\b", output)
        assert re.search(r"Total Plays:\s*98765\b", output)

    def test_shows_connector_breakdown(self):
        mock_result = DashboardStatsResult(
            total_tracks=100,
            total_plays=5000,
            total_playlists=10,
            total_liked=50,
            total_favorite_artists=7,
            tracks_by_connector={"spotify": 80},
            liked_by_connector={},
            plays_by_connector={"spotify": 5000},
            playlists_by_connector={},
            preference_counts={},
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
