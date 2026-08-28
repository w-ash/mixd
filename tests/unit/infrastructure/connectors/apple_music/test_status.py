"""Unit tests for the Apple Music connector status probe.

``get_apple_music_status`` (v0.11.x P4): honest token-storage-only probe —
no network calls. An expired or reauth-marked Music User Token stays
``connected=True`` with ``auth_error="reauth_required"`` so the UI derives
``needs_reauth`` (one-click fix), never ``expired``.
"""

import time
from unittest.mock import AsyncMock

from src.domain.entities.connector import derive_status_state
from src.infrastructure.connectors._shared.token_storage import StoredToken
from src.infrastructure.connectors.apple_music.status import get_apple_music_status


def make_storage(token: StoredToken | None) -> AsyncMock:
    storage = AsyncMock()
    storage.load_token = AsyncMock(return_value=token)
    return storage


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
