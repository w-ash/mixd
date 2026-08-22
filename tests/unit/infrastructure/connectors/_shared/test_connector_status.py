"""Unit tests for connector status probes.

``get_spotify_status`` scope read-back (v0.10.1): a stored grant narrower
than ``SPOTIFY_SCOPES`` surfaces as ``auth_error="scope_missing"`` while the
connection stays usable (``connected=True``), and ``refresh_failed`` keeps
precedence.

``get_apple_music_status`` (v0.11.x P4): honest token-storage-only probe —
no network calls. An expired or reauth-marked Music User Token stays
``connected=True`` with ``auth_error="reauth_required"`` so the UI derives
``needs_reauth`` (one-click fix), never ``expired``.
"""

import time
from unittest.mock import AsyncMock, patch

from src.domain.entities.connector import derive_status_state
from src.infrastructure.connectors._shared.connector_status import (
    get_apple_music_status,
    get_spotify_status,
)
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


def make_apple_token(
    *,
    expires_at: int | None = None,
    extra_data: dict[str, object] | None = None,
) -> StoredToken:
    token = StoredToken(
        access_token="fake-music-user-token",
        token_type="music_user_token",
        expires_at=expires_at if expires_at is not None else int(time.time()) + 3600,
    )
    if extra_data is not None:
        token["extra_data"] = extra_data
    return token


class TestAppleMusicStatus:
    async def test_no_token_is_disconnected(self) -> None:
        status = await get_apple_music_status("u1", make_storage(None))

        assert status.name == "apple_music"
        assert status.auth_method == "browser_bridge"
        assert status.connected is False
        assert status.auth_error is None
        assert derive_status_state(status) == "disconnected"

    async def test_fresh_token_is_connected_with_expiry(self) -> None:
        expires = int(time.time()) + 3600
        token = make_apple_token(expires_at=expires)
        status = await get_apple_music_status("u1", make_storage(token))

        assert status.connected is True
        assert status.token_expires_at == expires
        assert status.auth_error is None
        # Apple has no profile endpoint — no account name to show.
        assert status.account_name is None
        assert derive_status_state(status) == "connected"

    async def test_expired_token_needs_reauth_not_expired(self) -> None:
        token = make_apple_token(expires_at=int(time.time()) - 60)
        status = await get_apple_music_status("u1", make_storage(token))

        # MUTs can't refresh — expiry is expected credential aging, fixed by
        # one click. It must derive to needs_reauth, never "expired".
        assert status.connected is True
        assert status.auth_error == "reauth_required"
        assert derive_status_state(status) == "needs_reauth"

    async def test_reauth_marker_needs_reauth(self) -> None:
        token = make_apple_token(
            expires_at=int(time.time()) + 3600,
            extra_data={"reauth_required": True},
        )
        status = await get_apple_music_status("u1", make_storage(token))

        assert status.connected is True
        assert status.auth_error == "reauth_required"
        assert derive_status_state(status) == "needs_reauth"

    async def test_probe_makes_no_network_calls(self) -> None:
        # The probe's only I/O is the storage read — one load_token call.
        storage = make_storage(make_apple_token())
        _ = await get_apple_music_status("u1", storage)

        storage.load_token.assert_awaited_once_with("apple_music", "u1")


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
