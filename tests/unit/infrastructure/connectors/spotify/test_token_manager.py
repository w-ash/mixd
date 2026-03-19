"""Tests for SpotifyTokenManager with injected TokenStorage."""

import time
from unittest.mock import AsyncMock, patch

import pytest

from src.infrastructure.connectors._shared.token_storage import StoredToken
from src.infrastructure.connectors.spotify.auth import SpotifyTokenManager


def _make_token(*, expired: bool = False) -> StoredToken:
    """Create a test token, optionally expired."""
    expires_at = int(time.time()) + (3600 if not expired else -600)
    return StoredToken(
        access_token="access-123",
        refresh_token="refresh-456",
        token_type="Bearer",
        expires_in=3600,
        expires_at=expires_at,
        scope="playlist-read-private",
    )


class TestSpotifyTokenManager:
    """Tests for SpotifyTokenManager with mock TokenStorage."""

    @pytest.fixture
    def mock_storage(self):
        storage = AsyncMock()
        storage.load_token = AsyncMock(return_value=None)
        storage.save_token = AsyncMock()
        storage.delete_token = AsyncMock()
        return storage

    @pytest.fixture
    def manager(self, mock_storage):
        return SpotifyTokenManager(storage=mock_storage)

    async def test_get_valid_token_loads_from_storage(
        self, manager: SpotifyTokenManager, mock_storage: AsyncMock
    ):
        """First call should load from storage."""
        mock_storage.load_token.return_value = _make_token()

        token = await manager.get_valid_token()

        assert token == "access-123"
        mock_storage.load_token.assert_called_once_with("spotify")

    async def test_get_valid_token_caches_in_memory(
        self, manager: SpotifyTokenManager, mock_storage: AsyncMock
    ):
        """Second call should use in-memory cache, not hit storage again."""
        mock_storage.load_token.return_value = _make_token()

        await manager.get_valid_token()
        await manager.get_valid_token()

        mock_storage.load_token.assert_called_once()

    @patch.object(SpotifyTokenManager, "_refresh_token", new_callable=AsyncMock)
    async def test_refreshes_expired_token(
        self,
        mock_refresh: AsyncMock,
        manager: SpotifyTokenManager,
        mock_storage: AsyncMock,
    ):
        """Expired token should trigger a refresh and save."""
        mock_storage.load_token.return_value = _make_token(expired=True)
        refreshed = _make_token()
        mock_refresh.return_value = refreshed

        token = await manager.get_valid_token()

        assert token == "access-123"
        mock_refresh.assert_called_once_with("refresh-456")
        mock_storage.save_token.assert_called_once()

    async def test_try_silent_refresh_returns_none_when_no_token(
        self, manager: SpotifyTokenManager, mock_storage: AsyncMock
    ):
        """try_silent_refresh returns None when no stored token exists."""
        mock_storage.load_token.return_value = None

        result = await manager.try_silent_refresh()
        assert result is None

    async def test_try_silent_refresh_returns_valid_token(
        self, manager: SpotifyTokenManager, mock_storage: AsyncMock
    ):
        """try_silent_refresh returns cached token when not expired."""
        valid = _make_token()
        mock_storage.load_token.return_value = valid

        result = await manager.try_silent_refresh()
        assert result is not None
        assert result["access_token"] == "access-123"

    @patch.object(SpotifyTokenManager, "_refresh_token", new_callable=AsyncMock)
    async def test_force_refresh_saves_to_storage(
        self,
        mock_refresh: AsyncMock,
        manager: SpotifyTokenManager,
        mock_storage: AsyncMock,
    ):
        """force_refresh should persist the new token."""
        mock_storage.load_token.return_value = _make_token()
        refreshed = _make_token()
        mock_refresh.return_value = refreshed

        await manager.force_refresh()

        mock_storage.save_token.assert_called_once()

    async def test_force_refresh_raises_when_no_token(
        self, manager: SpotifyTokenManager, mock_storage: AsyncMock
    ):
        """force_refresh raises RuntimeError when no token exists."""
        mock_storage.load_token.return_value = None

        with pytest.raises(RuntimeError, match="No refresh token available"):
            await manager.force_refresh()
