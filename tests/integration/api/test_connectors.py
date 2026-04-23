"""Integration tests for connector status and Spotify-playlist endpoints.

Tests connector status detection using mocked TokenStorage, and the
Spotify-playlist browse + import routes using the ``mock_connector_provider``
fixture so no live API calls leak from the integration env's real OAuth tokens.
"""

from datetime import UTC, datetime
import time
from unittest.mock import AsyncMock, patch

import httpx

from src.infrastructure.connectors._shared.token_storage import StoredToken
from src.infrastructure.persistence.database.db_connection import get_session
from src.infrastructure.persistence.database.db_models import DBConnectorPlaylist

# Patch target prefix — logic lives in infrastructure alongside connectors
_SVC = "src.infrastructure.connectors._shared.connector_status"


class TestGetConnectors:
    """GET /api/v1/connectors returns connector status array."""

    async def test_returns_all_four_connectors(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/api/v1/connectors")

        assert response.status_code == 200
        connectors = response.json()
        assert isinstance(connectors, list)
        names = {c["name"] for c in connectors}
        assert names == {"spotify", "lastfm", "musicbrainz", "apple_music"}


def _mock_storage(
    spotify_token: StoredToken | None = None, lastfm_token: StoredToken | None = None
) -> AsyncMock:
    """Create a mock TokenStorage with configured return values."""
    storage = AsyncMock()

    async def _load(service: str, user_id: str) -> StoredToken | None:
        if service == "spotify":
            return spotify_token
        if service == "lastfm":
            return lastfm_token
        return None

    storage.load_token = AsyncMock(side_effect=_load)
    storage.save_token = AsyncMock()
    storage.delete_token = AsyncMock()
    return storage


class TestSpotifyStatus:
    """Spotify connector status detection from TokenStorage."""

    async def test_disconnected_when_no_token(self, client: httpx.AsyncClient) -> None:
        storage = _mock_storage(spotify_token=None)
        with patch(f"{_SVC}.get_token_storage", return_value=storage):
            response = await client.get("/api/v1/connectors")

        spotify = next(c for c in response.json() if c["name"] == "spotify")
        assert spotify["connected"] is False

    async def test_connected_with_valid_token(self, client: httpx.AsyncClient) -> None:
        token = StoredToken(
            access_token="test_token",
            refresh_token="test_refresh",
            expires_at=int(time.time()) + 3600,
            account_name="testuser",
        )
        storage = _mock_storage(spotify_token=token)
        with patch(f"{_SVC}.get_token_storage", return_value=storage):
            response = await client.get("/api/v1/connectors")

        spotify = next(c for c in response.json() if c["name"] == "spotify")
        assert spotify["connected"] is True
        assert spotify["token_expires_at"] is not None
        assert spotify["account_name"] == "testuser"

    async def test_failed_silent_refresh_reports_auth_error(
        self, client: httpx.AsyncClient
    ) -> None:
        """Expired access token + refresh attempt that returns None surfaces as an
        auth error, not a false-positive 'connected' state."""
        stale_expires = int(time.time()) - 3600
        token = StoredToken(
            access_token="test_token",
            refresh_token="test_refresh",
            expires_at=stale_expires,
        )
        storage = _mock_storage(spotify_token=token)

        # ``try_silent_refresh`` returning None simulates a revoked/invalid
        # refresh_token — we must not claim the user is still connected.
        mock_refresh = AsyncMock(return_value=None)

        with (
            patch(f"{_SVC}.get_token_storage", return_value=storage),
            patch(
                "src.infrastructure.connectors.spotify.auth.SpotifyTokenManager.try_silent_refresh",
                mock_refresh,
            ),
        ):
            response = await client.get("/api/v1/connectors")

        spotify = next(c for c in response.json() if c["name"] == "spotify")
        assert spotify["connected"] is False
        assert spotify["auth_error"] == "refresh_failed"
        assert spotify["status"] == "error"

    async def test_disconnected_without_refresh_token(
        self, client: httpx.AsyncClient
    ) -> None:
        """No refresh_token means the connection can't be sustained."""
        token = StoredToken(
            access_token="test_token",
            expires_at=int(time.time()) + 3600,
        )
        storage = _mock_storage(spotify_token=token)
        with patch(f"{_SVC}.get_token_storage", return_value=storage):
            response = await client.get("/api/v1/connectors")

        spotify = next(c for c in response.json() if c["name"] == "spotify")
        assert spotify["connected"] is False


class TestSpotifyDisplayName:
    """Spotify display_name fetching and caching."""

    async def test_display_name_from_stored_token(
        self, client: httpx.AsyncClient
    ) -> None:
        """Cached account_name returned without any HTTP call."""
        token = StoredToken(
            access_token="test_token",
            refresh_token="test_refresh",
            expires_at=int(time.time()) + 3600,
            account_name="cached_user",
        )
        storage = _mock_storage(spotify_token=token)
        with patch(f"{_SVC}.get_token_storage", return_value=storage):
            response = await client.get("/api/v1/connectors")

        spotify = next(c for c in response.json() if c["name"] == "spotify")
        assert spotify["account_name"] == "cached_user"

    async def test_fetches_display_name_when_not_cached(
        self, client: httpx.AsyncClient
    ) -> None:
        """First visit with valid token fetches display_name and caches it."""
        token = StoredToken(
            access_token="test_token",
            refresh_token="test_refresh",
            expires_at=int(time.time()) + 3600,
        )
        storage = _mock_storage(spotify_token=token)
        mock_fetch = AsyncMock(return_value="fetched_user")
        with (
            patch(f"{_SVC}.get_token_storage", return_value=storage),
            patch(f"{_SVC}.fetch_spotify_display_name", mock_fetch),
        ):
            response = await client.get("/api/v1/connectors")

        spotify = next(c for c in response.json() if c["name"] == "spotify")
        assert spotify["account_name"] == "fetched_user"
        mock_fetch.assert_called_once_with("test_token")

        # Verify it was saved back to storage with account_name
        storage.save_token.assert_called()

    async def test_display_name_fetch_failure_returns_none(
        self, client: httpx.AsyncClient
    ) -> None:
        """Failed display_name fetch returns null — still connected."""
        token = StoredToken(
            access_token="test_token",
            refresh_token="test_refresh",
            expires_at=int(time.time()) + 3600,
        )
        storage = _mock_storage(spotify_token=token)
        mock_fetch = AsyncMock(return_value=None)
        with (
            patch(f"{_SVC}.get_token_storage", return_value=storage),
            patch(f"{_SVC}.fetch_spotify_display_name", mock_fetch),
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

        apple = next(c for c in response.json() if c["name"] == "apple_music")
        assert apple["connected"] is False
        assert apple["account_name"] is None


class TestLastfmStatus:
    """Last.fm connector status from TokenStorage + settings."""

    async def test_status_reflects_settings(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/api/v1/connectors")

        lastfm = next(c for c in response.json() if c["name"] == "lastfm")
        # Result depends on env vars — just verify shape
        assert "connected" in lastfm
        assert "account_name" in lastfm


class TestSpotifyPlaylistBrowse:
    """GET /api/v1/connectors/spotify/playlists — route-level behavior."""

    async def test_cache_hit_returns_seeded_playlists(
        self, client: httpx.AsyncClient
    ) -> None:
        """Default (force_refresh=false) reads from DBConnectorPlaylist.

        No connector touched on the cache-hit path — proves the route's
        cache-first contract holds without the fixture having to stub a
        connector.
        """
        async with get_session() as session:
            session.add(
                DBConnectorPlaylist(
                    connector_name="spotify",
                    connector_playlist_identifier="sp_test_1",
                    name="Cached Playlist",
                    description=None,
                    owner="alice",
                    owner_id="alice_id",
                    is_public=True,
                    collaborative=False,
                    follower_count=0,
                    items=[],
                    raw_metadata={"total_tracks": 42},
                    last_updated=datetime.now(UTC),
                )
            )
            await session.commit()

        response = await client.get("/api/v1/connectors/spotify/playlists")

        assert response.status_code == 200
        body = response.json()
        assert body["from_cache"] is True
        assert len(body["data"]) == 1
        row = body["data"][0]
        assert row["name"] == "Cached Playlist"
        assert row["track_count"] == 42
        assert row["import_status"] == "not_imported"

    async def test_force_refresh_with_failing_connector_returns_error_envelope(
        self,
        client: httpx.AsyncClient,
        mock_connector_provider: dict[str, object],
    ) -> None:
        """force_refresh=true with a stubbed-failing connector returns the error envelope.

        Proves the test seam blocks live API calls — the stub's
        ``fetch_user_playlists`` raises before any HTTP request would happen.
        Uses ``ValueError`` because it has a registered exception handler that
        produces the standard ``{error: {code, message}}`` envelope; arbitrary
        ``Exception`` subclasses don't surface cleanly through httpx's
        ASGITransport in test mode.
        """
        spotify = AsyncMock()
        spotify.fetch_user_playlists = AsyncMock(
            side_effect=ValueError("connector temporarily unavailable")
        )
        mock_connector_provider["spotify"] = spotify

        response = await client.get(
            "/api/v1/connectors/spotify/playlists?force_refresh=true"
        )

        assert response.status_code == 400
        body = response.json()
        assert body["error"]["code"] == "VALIDATION_ERROR"
        spotify.fetch_user_playlists.assert_called_once()


class TestSpotifyPlaylistImport:
    """POST /api/v1/connectors/spotify/playlists/import — route-level behavior."""

    async def test_single_playlist_returns_202_with_operation_id(
        self, client: httpx.AsyncClient
    ) -> None:
        """One-element ids list returns the 202 OperationStartedResponse shape.

        Background work is stubbed by ``_noop_launch`` in conftest, so the
        route's contract (202 + operation_id) is exercised without spawning
        the real import task.
        """
        response = await client.post(
            "/api/v1/connectors/spotify/playlists/import",
            json={
                "connector_playlist_ids": ["sp_single"],
                "sync_direction": "pull",
            },
        )

        assert response.status_code == 202
        body = response.json()
        assert isinstance(body.get("operation_id"), str)
        assert body["operation_id"]  # non-empty

    async def test_empty_ids_returns_422_before_use_case(
        self, client: httpx.AsyncClient
    ) -> None:
        """Empty connector_playlist_ids fails Pydantic validation (min_length=1)."""
        response = await client.post(
            "/api/v1/connectors/spotify/playlists/import",
            json={
                "connector_playlist_ids": [],
                "sync_direction": "pull",
            },
        )

        assert response.status_code == 422

    async def test_invalid_sync_direction_returns_422(
        self, client: httpx.AsyncClient
    ) -> None:
        """sync_direction must be 'pull' or 'push' — anything else fails validation."""
        response = await client.post(
            "/api/v1/connectors/spotify/playlists/import",
            json={
                "connector_playlist_ids": ["sp_x"],
                "sync_direction": "mirror",
            },
        )

        assert response.status_code == 422

    async def test_force_flag_accepted_in_request_body(
        self, client: httpx.AsyncClient
    ) -> None:
        """``force: true`` is accepted alongside the other fields and returns 202.

        End-to-end behavior (force actually re-fetches a cached playlist)
        is exercised at the use-case unit level; here we just verify the
        route schema accepts the field.
        """
        response = await client.post(
            "/api/v1/connectors/spotify/playlists/import",
            json={
                "connector_playlist_ids": ["sp_x"],
                "sync_direction": "pull",
                "force": True,
            },
        )

        assert response.status_code == 202
        assert isinstance(response.json().get("operation_id"), str)
