"""Unit tests for ``get_spotify_status`` scope read-back (v0.10.1).

Verifies the scope-gap detection added for the recently-played re-consent
flow: a stored grant narrower than ``SPOTIFY_SCOPES`` surfaces as
``auth_error="scope_missing"`` while the connection stays usable
(``connected=True``), and ``refresh_failed`` keeps precedence.
"""

import time
from unittest.mock import AsyncMock, patch

from src.infrastructure.connectors._shared.connector_status import get_spotify_status
from src.infrastructure.connectors._shared.token_storage import StoredToken
from src.infrastructure.connectors.spotify.auth import (
    SPOTIFY_SCOPES,
    SpotifyTokenManager,
)

FULL_SCOPE = " ".join(SPOTIFY_SCOPES)


def make_storage(token: StoredToken | None) -> AsyncMock:
    storage = AsyncMock()
    storage.load_token = AsyncMock(return_value=token)
    storage.save_token = AsyncMock()
    return storage


def make_token(
    *, expires_at: int | None = None, scope: str | None = None
) -> StoredToken:
    token = StoredToken(
        access_token="access",
        refresh_token="refresh",
        expires_at=expires_at if expires_at is not None else int(time.time()) + 3600,
        account_name="testuser",
    )
    if scope is not None:
        token["scope"] = scope
    return token


class TestScopeGapDetection:
    async def test_stale_scope_reports_scope_missing_but_stays_connected(self) -> None:
        token = make_token(scope="user-library-read playlist-read-private")
        status = await get_spotify_status("u1", storage=make_storage(token))

        assert status.auth_error == "scope_missing"
        assert status.connected is True
        assert status.account_name == "testuser"

    async def test_legacy_token_without_scope_key_reports_scope_missing(self) -> None:
        status = await get_spotify_status("u1", storage=make_storage(make_token()))

        assert status.auth_error == "scope_missing"
        assert status.connected is True

    async def test_full_scope_token_is_clean(self) -> None:
        token = make_token(scope=FULL_SCOPE)
        status = await get_spotify_status("u1", storage=make_storage(token))

        assert status.auth_error is None
        assert status.connected is True

    async def test_no_token_is_disconnected_without_error(self) -> None:
        status = await get_spotify_status("u1", storage=make_storage(None))

        assert status.connected is False
        assert status.auth_error is None


class TestRefreshInteraction:
    async def test_refresh_failure_takes_precedence_over_scope_gap(self) -> None:
        token = make_token(expires_at=int(time.time()) - 3600, scope="old-scope")
        with patch.object(
            SpotifyTokenManager, "try_silent_refresh", AsyncMock(return_value=None)
        ):
            status = await get_spotify_status("u1", storage=make_storage(token))

        assert status.auth_error == "refresh_failed"
        assert status.connected is False

    async def test_refreshed_scope_is_authoritative_for_gap_check(self) -> None:
        # Stored token has a stale scope, but Spotify echoes the real grant
        # on refresh — the refreshed scope must win the comparison.
        token = make_token(expires_at=int(time.time()) - 3600, scope="old-scope")
        refreshed = {
            "access_token": "new-access",
            "refresh_token": "refresh",
            "expires_at": int(time.time()) + 3600,
            "scope": FULL_SCOPE,
        }
        with patch.object(
            SpotifyTokenManager, "try_silent_refresh", AsyncMock(return_value=refreshed)
        ):
            status = await get_spotify_status("u1", storage=make_storage(token))

        assert status.auth_error is None
        assert status.connected is True
