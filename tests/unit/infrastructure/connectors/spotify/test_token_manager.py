"""Tests for SpotifyTokenManager with injected TokenStorage."""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.infrastructure.connectors._shared.token_storage import StoredToken
from src.infrastructure.connectors.spotify.auth import SpotifyTokenManager

_UID = "test-user"


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
        return SpotifyTokenManager(storage=mock_storage, user_id=_UID)

    async def test_get_valid_token_loads_from_storage(
        self, manager: SpotifyTokenManager, mock_storage: AsyncMock
    ):
        """First call should load from storage."""
        mock_storage.load_token.return_value = _make_token()

        token = await manager.get_valid_token()

        assert token == "access-123"
        mock_storage.load_token.assert_called_once_with("spotify", _UID)

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

    async def test_get_valid_token_raises_when_no_token_instead_of_browser_auth(
        self, manager: SpotifyTokenManager, mock_storage: AsyncMock
    ):
        """Server-safe: with no stored token, get_valid_token raises rather than
        launching the blocking browser OAuth flow inside the FastAPI worker."""
        from src.domain.exceptions import SpotifyAuthRequiredError

        mock_storage.load_token.return_value = None

        with pytest.raises(SpotifyAuthRequiredError):
            await manager.get_valid_token()

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


class TestRefreshPreservesGrant:
    """A refresh response must never look like the user revoked their grant.

    Spotify omits fields it considers unchanged, and the response is persisted
    verbatim. ``refresh_token`` was already carried forward on omission;
    ``scope`` was not, so a refresh could silently blank the stored grant — after
    which every scope check reads "nothing granted" and the user is told to
    reconnect, and the scope-gated routes 409, while their authorization is
    entirely intact.
    """

    @pytest.fixture
    def manager(self):
        return SpotifyTokenManager(storage=AsyncMock(), user_id=_UID)

    async def _refresh_with(
        self, manager: SpotifyTokenManager, response: dict[str, object]
    ) -> StoredToken:
        manager._token_info = _make_token()
        with (
            patch(
                "src.infrastructure.connectors.spotify.auth.make_spotify_auth_client"
            ) as mock_client,
            patch(
                "src.infrastructure.connectors.spotify.auth.parse_json_response",
                return_value=dict(response),
            ),
        ):
            # MagicMock response, not AsyncMock: the production code calls
            # `response.raise_for_status()` synchronously, and an AsyncMock would
            # hand back an un-awaited coroutine instead.
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=MagicMock()
            )
            return await manager._refresh_token("refresh-456")

    async def test_omitted_scope_carries_the_previous_grant_forward(
        self, manager: SpotifyTokenManager
    ) -> None:
        refreshed = await self._refresh_with(
            manager, {"access_token": "new-access", "expires_in": 3600}
        )
        assert refreshed["scope"] == "playlist-read-private"

    async def test_returned_scope_wins_over_the_previous_grant(
        self, manager: SpotifyTokenManager
    ) -> None:
        # A genuinely changed grant must not be masked by the carry-forward.
        refreshed = await self._refresh_with(
            manager,
            {
                "access_token": "new-access",
                "expires_in": 3600,
                "scope": "playlist-read-private user-read-recently-played",
            },
        )
        assert refreshed["scope"] == "playlist-read-private user-read-recently-played"

    async def test_omitted_refresh_token_is_still_preserved(
        self, manager: SpotifyTokenManager
    ) -> None:
        refreshed = await self._refresh_with(
            manager, {"access_token": "new-access", "expires_in": 3600}
        )
        assert refreshed["refresh_token"] == "refresh-456"
