"""Tests for POST /api/v1/playlists/backup endpoint."""

from unittest.mock import AsyncMock, patch

import httpx

from src.application.use_cases.create_canonical_playlist import (
    CreateCanonicalPlaylistResult,
)
from tests.fixtures import make_playlist


class TestPlaylistBackupEndpoint:
    async def test_backup_returns_201_with_playlist(self, client: httpx.AsyncClient):
        playlist = make_playlist(name="Test Playlist")
        mock_result = CreateCanonicalPlaylistResult(playlist=playlist, tracks_created=5)

        with patch(
            "src.application.services.playlist_backup_service.run_playlist_backup",
            new_callable=AsyncMock,
            return_value=mock_result,
        ) as mock_backup:
            resp = await client.post(
                "/api/v1/playlists/backup",
                json={"connector": "spotify", "playlist_id": "abc123"},
            )

            assert resp.status_code == 201
            data = resp.json()
            assert data["name"] == "Test Playlist"
            mock_backup.assert_awaited_once_with(
                connector_name="spotify", playlist_id="abc123"
            )

    async def test_backup_missing_fields_returns_422(self, client: httpx.AsyncClient):
        resp = await client.post("/api/v1/playlists/backup", json={})
        assert resp.status_code == 422

    async def test_backup_connector_error_returns_500(self, client: httpx.AsyncClient):
        with patch(
            "src.application.services.playlist_backup_service.run_playlist_backup",
            new_callable=AsyncMock,
            side_effect=ValueError("Unknown connector: badservice"),
        ):
            resp = await client.post(
                "/api/v1/playlists/backup",
                json={"connector": "badservice", "playlist_id": "xyz"},
            )
            assert resp.status_code in (400, 500)
