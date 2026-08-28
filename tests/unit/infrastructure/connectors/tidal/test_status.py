"""Unit tests for the Tidal connector status probe.

Storage-only probe (v0.11.3 T4) — the bearer auth refreshes on use, so the
probe never spends a refresh POST (no Spotify-style silent refresh). The
``detail`` suffix renders the cached ``favorites_count`` (format cases
covered by the ``stored_token_status`` primitive tests).
"""

import time
from unittest.mock import AsyncMock

from src.domain.entities.connector import derive_status_state
from src.infrastructure.connectors._shared.token_storage import StoredToken
from src.infrastructure.connectors.tidal.status import get_tidal_status


def make_storage(token: StoredToken | None) -> AsyncMock:
    storage = AsyncMock()
    storage.load_token = AsyncMock(return_value=token)
    return storage


_TIDAL_RT = "rt-1"


def _tidal_token(
    *,
    expires_at: int | None = None,
    refresh_token: str | None = _TIDAL_RT,
    extra_data: dict[str, object] | None = None,
) -> StoredToken:
    token = StoredToken(
        access_token="tidal-at",
        token_type="Bearer",
        expires_at=expires_at if expires_at is not None else int(time.time()) + 3600,
    )
    if refresh_token is not None:
        token["refresh_token"] = refresh_token
    if extra_data is not None:
        token["extra_data"] = extra_data
    return token


class TestGetTidalStatus:
    async def test_no_token_disconnected(self) -> None:
        status = await get_tidal_status("u1", make_storage(None))

        assert status.name == "tidal"
        assert status.auth_method == "oauth"
        assert status.connected is False
        assert status.auth_error is None
        assert derive_status_state(status) == "disconnected"

    async def test_fresh_token_connected_with_expiry(self) -> None:
        expires = int(time.time()) + 3600
        status = await get_tidal_status(
            "u1", make_storage(_tidal_token(expires_at=expires))
        )

        assert status.connected is True
        assert status.token_expires_at == expires
        assert status.auth_error is None
        assert derive_status_state(status) == "connected"

    async def test_stray_reauth_marker_is_ignored(self) -> None:
        # No Tidal code path writes a reauth marker (a dead grant is
        # compare-and-DELETED on invalid_grant), so a stray marker on an
        # otherwise-healthy token must not fabricate a reauth prompt.
        token = _tidal_token(extra_data={"reauth_required": True})
        status = await get_tidal_status("u1", make_storage(token))

        assert status.connected is True
        assert status.auth_error is None
        assert derive_status_state(status) == "connected"

    async def test_expired_without_refresh_token_needs_reauth(self) -> None:
        # No refresh token to renew with — only the reconnect flow fixes it,
        # so it must derive to needs_reauth (one click), never "expired".
        token = _tidal_token(expires_at=int(time.time()) - 60, refresh_token=None)
        status = await get_tidal_status("u1", make_storage(token))

        assert status.connected is True
        assert status.auth_error == "reauth_required"
        assert derive_status_state(status) == "needs_reauth"

    async def test_expired_with_refresh_token_is_not_reauth(self) -> None:
        # An expired access token beside a live refresh token is routine —
        # the bearer auth refreshes on next use; the probe stays cheap and
        # reports the stored expiry as-is.
        token = _tidal_token(expires_at=int(time.time()) - 60)
        status = await get_tidal_status("u1", make_storage(token))

        assert status.connected is True
        assert status.auth_error is None

    async def test_detail_renders_cached_favorites_count(self) -> None:
        token = _tidal_token(extra_data={"favorites_count": 1204})
        status = await get_tidal_status("u1", make_storage(token))

        assert status.detail == "1,204 favorites"

    async def test_probe_is_storage_only(self) -> None:
        storage = make_storage(_tidal_token())
        _ = await get_tidal_status("u1", storage)

        storage.load_token.assert_awaited_once_with("tidal", "u1")
