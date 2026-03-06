"""Integration tests for dashboard statistics API endpoint.

Tests the full request → route handler → use case → database → response cycle
for GET /api/v1/stats/dashboard.
"""

import httpx


class TestGetDashboardStats:
    """GET /api/v1/stats/dashboard returns aggregate library statistics."""

    async def test_get_dashboard_stats_200(self, client: httpx.AsyncClient) -> None:
        """Returns 200 with all expected fields."""
        response = await client.get("/api/v1/stats/dashboard")

        assert response.status_code == 200
        body = response.json()
        assert "total_tracks" in body
        assert "total_plays" in body
        assert "total_playlists" in body
        assert "total_liked" in body
        assert "tracks_by_connector" in body
        assert "liked_by_connector" in body

    async def test_get_dashboard_stats_empty_db(
        self, client: httpx.AsyncClient
    ) -> None:
        """Empty database returns all zeros and empty dicts."""
        response = await client.get("/api/v1/stats/dashboard")

        body = response.json()
        assert body["total_tracks"] == 0
        assert body["total_plays"] == 0
        assert body["total_playlists"] == 0
        assert body["total_liked"] == 0
        assert body["tracks_by_connector"] == {}
        assert body["liked_by_connector"] == {}
