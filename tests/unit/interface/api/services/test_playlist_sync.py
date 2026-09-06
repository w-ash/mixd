"""Unit tests for the sync route's synchronous 409 pre-flight + confirm token.

The migrated ``POST /playlists/{id}/links/{link_id}/sync`` route runs a read-only
preview synchronously and raises ``ConfirmationRequiredError`` (mapped to HTTP 409
by middleware) when a destructive sync's ``confirm_token`` is missing or stale —
so the confirm round-trip is reachable at request time, and a confirmation for an
out-of-date plan is rejected rather than silently applied.
"""

from collections.abc import Awaitable, Callable
from unittest.mock import AsyncMock, patch
from uuid import uuid7

import pytest

from src.application.use_cases.preview_playlist_sync import (
    PreviewPlaylistSyncCommand,
    PreviewPlaylistSyncResult,
    PreviewPlaylistSyncUseCase,
)
from src.domain.entities.playlist_link import SyncDirection
from src.domain.exceptions import ConfirmationRequiredError
from src.interface.api.services.playlist_sync import _ensure_sync_confirmed

_EXEC = "src.interface.api.services.playlist_sync.execute_use_case"
_FRESH_TOKEN = "tok-fresh"


def _preview(*, flagged: bool) -> PreviewPlaylistSyncResult:
    return PreviewPlaylistSyncResult(
        tracks_to_add=0,
        tracks_to_remove=40 if flagged else 1,
        tracks_unchanged=10,
        direction=SyncDirection.PUSH,
        connector_name="spotify",
        playlist_name="Mix",
        safety_flagged=flagged,
        safety_message="This will remove 40 of 50 tracks. 10 will remain."
        if flagged
        else None,
        safety_removals=40 if flagged else 1,
        safety_total=50 if flagged else 11,
        safety_remaining=10,
        confirm_token=_FRESH_TOKEN,
    )


class TestEnsureSyncConfirmed:
    async def test_destructive_without_token_raises_with_fresh_token(self) -> None:
        with patch(_EXEC, new=AsyncMock(return_value=_preview(flagged=True))):
            with pytest.raises(ConfirmationRequiredError) as exc_info:
                await _ensure_sync_confirmed(uuid7(), None, "u", None, None)

        err = exc_info.value
        assert err.removals == 40
        assert err.total == 50
        assert err.remaining == 10
        # The fresh token the client must echo back to proceed.
        assert err.confirm_token == _FRESH_TOKEN

    async def test_destructive_with_stale_token_raises(self) -> None:
        with patch(_EXEC, new=AsyncMock(return_value=_preview(flagged=True))):
            with pytest.raises(ConfirmationRequiredError):
                await _ensure_sync_confirmed(uuid7(), None, "u", "stale-token", None)

    async def test_destructive_with_matching_token_proceeds(self) -> None:
        with patch(_EXEC, new=AsyncMock(return_value=_preview(flagged=True))):
            # Matching token == the user confirmed THIS plan — must not raise.
            assert (
                await _ensure_sync_confirmed(uuid7(), None, "u", _FRESH_TOKEN, None)
                is None
            )

    async def test_non_destructive_does_not_raise(self) -> None:
        with patch(_EXEC, new=AsyncMock(return_value=_preview(flagged=False))):
            assert await _ensure_sync_confirmed(uuid7(), None, "u", None, None) is None

    async def test_playlist_scope_reaches_the_preview_command(self) -> None:
        """The nested route's playlist_id scopes the pre-flight preview too."""
        captured: list[PreviewPlaylistSyncCommand] = []

        async def _run(
            factory: Callable[[object], Awaitable[object]], **_kwargs: object
        ) -> PreviewPlaylistSyncResult:
            with patch.object(
                PreviewPlaylistSyncUseCase,
                "execute",
                AsyncMock(side_effect=lambda cmd, _uow: captured.append(cmd)),
            ):
                await factory(object())
            return _preview(flagged=False)

        playlist_id = uuid7()
        with patch(_EXEC, new=AsyncMock(side_effect=_run)):
            await _ensure_sync_confirmed(uuid7(), None, "u", None, playlist_id)

        assert [cmd.playlist_id for cmd in captured] == [playlist_id]
