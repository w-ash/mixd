"""Integration tests for connector status endpoints.

Tests filesystem-based connector status detection using monkeypatched
paths and environment variables.
"""

import json
from pathlib import Path
import time
from unittest.mock import AsyncMock, patch

import httpx


class TestGetConnectors:
    """GET /api/v1/connectors returns connector status array."""

    async def test_returns_all_four_connectors(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/api/v1/connectors")

        assert response.status_code == 200
        connectors = response.json()
        assert isinstance(connectors, list)
        names = {c["name"] for c in connectors}
        assert names == {"spotify", "lastfm", "musicbrainz", "apple"}


class TestSpotifyStatus:
    """Spotify connector status detection from .spotify_cache file."""

    async def test_disconnected_when_no_cache(
        self, client: httpx.AsyncClient, tmp_path: Path
    ) -> None:
        with patch(
            "src.interface.api.routes.connectors.Path",
            return_value=tmp_path / "nonexistent",
        ):
            response = await client.get("/api/v1/connectors")

        spotify = next(c for c in response.json() if c["name"] == "spotify")
        assert spotify["connected"] is False

    async def test_connected_with_valid_cache(
        self, client: httpx.AsyncClient, tmp_path: Path
    ) -> None:
        cache_file = tmp_path / ".spotify_cache"
        cache_file.write_text(
            json.dumps({
                "access_token": "test_token",
                "expires_at": int(time.time()) + 3600,
                "refresh_token": "test_refresh",
                "display_name": "testuser",
            })
        )
        with patch(
            "src.interface.api.routes.connectors.Path",
            return_value=cache_file,
        ):
            response = await client.get("/api/v1/connectors")

        spotify = next(c for c in response.json() if c["name"] == "spotify")
        assert spotify["connected"] is True
        assert spotify["token_expires_at"] is not None

    async def test_connected_with_expired_token_but_refresh_token(
        self, client: httpx.AsyncClient, tmp_path: Path
    ) -> None:
        """Expired access token is still 'connected' — SpotifyTokenManager auto-refreshes."""
        cache_file = tmp_path / ".spotify_cache"
        stale_expires = int(time.time()) - 3600
        cache_file.write_text(
            json.dumps({
                "access_token": "test_token",
                "expires_at": stale_expires,
                "refresh_token": "test_refresh",
            })
        )
        with (
            patch(
                "src.interface.api.routes.connectors.Path",
                return_value=cache_file,
            ),
            patch(
                "src.interface.api.routes.connectors._try_refresh_spotify_token",
                return_value=(None, None),
            ),
        ):
            response = await client.get("/api/v1/connectors")

        spotify = next(c for c in response.json() if c["name"] == "spotify")
        assert spotify["connected"] is True
        assert spotify["token_expires_at"] == stale_expires

    async def test_expired_token_refresh_returns_fresh_expires(
        self, client: httpx.AsyncClient, tmp_path: Path
    ) -> None:
        """Silent refresh updates token_expires_at to a future timestamp."""
        cache_file = tmp_path / ".spotify_cache"
        fresh_expires = int(time.time()) + 3600
        cache_file.write_text(
            json.dumps({
                "access_token": "test_token",
                "expires_at": int(time.time()) - 3600,
                "refresh_token": "test_refresh",
            })
        )
        with (
            patch(
                "src.interface.api.routes.connectors.Path",
                return_value=cache_file,
            ),
            patch(
                "src.interface.api.routes.connectors._try_refresh_spotify_token",
                return_value=(fresh_expires, "refreshed_user"),
            ),
        ):
            response = await client.get("/api/v1/connectors")

        spotify = next(c for c in response.json() if c["name"] == "spotify")
        assert spotify["connected"] is True
        assert spotify["token_expires_at"] == fresh_expires
        assert spotify["token_expires_at"] > time.time()
        assert spotify["account_name"] == "refreshed_user"

    async def test_expired_token_refresh_failure_still_connected(
        self, client: httpx.AsyncClient, tmp_path: Path
    ) -> None:
        """Failed silent refresh falls through with stale data — still connected."""
        cache_file = tmp_path / ".spotify_cache"
        stale_expires = int(time.time()) - 3600
        cache_file.write_text(
            json.dumps({
                "access_token": "test_token",
                "expires_at": stale_expires,
                "refresh_token": "test_refresh",
            })
        )
        with (
            patch(
                "src.interface.api.routes.connectors.Path",
                return_value=cache_file,
            ),
            patch(
                "src.interface.api.routes.connectors._try_refresh_spotify_token",
                return_value=(None, None),
            ),
        ):
            response = await client.get("/api/v1/connectors")

        spotify = next(c for c in response.json() if c["name"] == "spotify")
        assert spotify["connected"] is True
        assert spotify["token_expires_at"] == stale_expires

    async def test_disconnected_without_refresh_token(
        self, client: httpx.AsyncClient, tmp_path: Path
    ) -> None:
        """No refresh_token means the connection can't be sustained."""
        cache_file = tmp_path / ".spotify_cache"
        cache_file.write_text(
            json.dumps({
                "access_token": "test_token",
                "expires_at": int(time.time()) + 3600,
            })
        )
        with patch(
            "src.interface.api.routes.connectors.Path",
            return_value=cache_file,
        ):
            response = await client.get("/api/v1/connectors")

        spotify = next(c for c in response.json() if c["name"] == "spotify")
        assert spotify["connected"] is False


class TestSpotifyDisplayName:
    """Spotify display_name fetching and caching."""

    async def test_display_name_from_cache(
        self, client: httpx.AsyncClient, tmp_path: Path
    ) -> None:
        """Cached display_name returned without any HTTP call."""
        cache_file = tmp_path / ".spotify_cache"
        cache_file.write_text(
            json.dumps({
                "access_token": "test_token",
                "expires_at": int(time.time()) + 3600,
                "refresh_token": "test_refresh",
                "display_name": "cached_user",
            })
        )
        with patch(
            "src.interface.api.routes.connectors.Path",
            return_value=cache_file,
        ):
            response = await client.get("/api/v1/connectors")

        spotify = next(c for c in response.json() if c["name"] == "spotify")
        assert spotify["account_name"] == "cached_user"

    async def test_fetches_display_name_when_not_cached(
        self, client: httpx.AsyncClient, tmp_path: Path
    ) -> None:
        """First visit with valid token fetches display_name and caches it."""
        cache_file = tmp_path / ".spotify_cache"
        cache_file.write_text(
            json.dumps({
                "access_token": "test_token",
                "expires_at": int(time.time()) + 3600,
                "refresh_token": "test_refresh",
            })
        )
        mock_fetch = AsyncMock(return_value="fetched_user")
        with (
            patch(
                "src.interface.api.routes.connectors.Path",
                return_value=cache_file,
            ),
            patch(
                "src.interface.api.routes.connectors._fetch_spotify_display_name",
                mock_fetch,
            ),
        ):
            response = await client.get("/api/v1/connectors")

        spotify = next(c for c in response.json() if c["name"] == "spotify")
        assert spotify["account_name"] == "fetched_user"
        mock_fetch.assert_called_once_with("test_token")

        # Verify it was cached back to file
        updated_cache = json.loads(cache_file.read_text())
        assert updated_cache["display_name"] == "fetched_user"

    async def test_display_name_fetch_failure_returns_none(
        self, client: httpx.AsyncClient, tmp_path: Path
    ) -> None:
        """Failed display_name fetch returns null — still connected."""
        cache_file = tmp_path / ".spotify_cache"
        cache_file.write_text(
            json.dumps({
                "access_token": "test_token",
                "expires_at": int(time.time()) + 3600,
                "refresh_token": "test_refresh",
            })
        )
        mock_fetch = AsyncMock(return_value=None)
        with (
            patch(
                "src.interface.api.routes.connectors.Path",
                return_value=cache_file,
            ),
            patch(
                "src.interface.api.routes.connectors._fetch_spotify_display_name",
                mock_fetch,
            ),
        ):
            response = await client.get("/api/v1/connectors")

        spotify = next(c for c in response.json() if c["name"] == "spotify")
        assert spotify["connected"] is True
        assert spotify["account_name"] is None


class TestMusicBrainzStatus:
    """MusicBrainz connector — always connected (public API)."""

    async def test_always_connected(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/api/v1/connectors")

        mb = next(c for c in response.json() if c["name"] == "musicbrainz")
        assert mb["connected"] is True
        assert mb["account_name"] is None
        assert mb["token_expires_at"] is None


class TestAppleMusicStatus:
    """Apple Music connector — stub, always not connected."""

    async def test_not_connected(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/api/v1/connectors")

        apple = next(c for c in response.json() if c["name"] == "apple")
        assert apple["connected"] is False
        assert apple["account_name"] is None


class TestLastfmStatus:
    """Last.fm connector status from settings."""

    async def test_status_reflects_settings(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/api/v1/connectors")

        lastfm = next(c for c in response.json() if c["name"] == "lastfm")
        # Result depends on env vars — just verify shape
        assert "connected" in lastfm
        assert "account_name" in lastfm
