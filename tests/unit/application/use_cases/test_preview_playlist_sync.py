"""Unit tests for PreviewPlaylistSyncUseCase — maps a SyncPlan to the result.

The engine's diff/safety behaviour is tested in
test_playlist_reconciliation_engine; here we verify the read-only mapping of a
SyncPlan into PreviewPlaylistSyncResult (counts, safety flag, direction).
"""

from unittest.mock import AsyncMock, patch
from uuid import uuid7

import pytest

from src.application.services.playlist_reconciliation_engine import (
    PlaylistReconciliationEngine,
    SyncPreview,
)
from src.application.use_cases.preview_playlist_sync import (
    PreviewPlaylistSyncCommand,
    PreviewPlaylistSyncUseCase,
)
from src.domain.entities.playlist import Playlist
from src.domain.entities.playlist_link import PlaylistLink, SyncDirection
from src.domain.exceptions import NotFoundError
from src.domain.playlist.reconciliation import SyncPlan
from src.domain.playlist.sync_safety import SyncSafetyResult
from tests.fixtures import TEST_USER_ID, make_mock_uow


def _link() -> PlaylistLink:
    return PlaylistLink(
        id=uuid7(),
        playlist_id=uuid7(),
        connector_name="spotify",
        connector_playlist_identifier="ext1",
        sync_direction=SyncDirection.PULL,
    )


async def test_preview_maps_plan_and_safety_gate_to_result():
    """The confirm dialog reads "remove N of M (K remain)" from these fields."""
    link = _link()  # stored direction is PULL
    uow = make_mock_uow()
    uow.get_playlist_link_repository().get_link = AsyncMock(return_value=link)
    uow.get_playlist_repository().get_playlist_by_id = AsyncMock(
        return_value=Playlist(name="My Playlist", user_id=TEST_USER_ID)
    )
    plan = SyncPlan(
        direction=SyncDirection.PUSH,
        tracks_to_add=2,
        tracks_to_remove=30,
        tracks_unchanged=10,
        is_noop=False,
        safety=SyncSafetyResult(
            flagged=True,
            reason="Would remove 30 of 40 tracks",
            removals=30,
            total_current=40,
            remaining_after_sync=10,
        ),
    )
    engine_preview = AsyncMock(
        return_value=SyncPreview(plan=plan, confirm_token="tok-123")
    )

    with patch.object(PlaylistReconciliationEngine, "preview", engine_preview):
        result = await PreviewPlaylistSyncUseCase().execute(
            PreviewPlaylistSyncCommand(
                user_id="u", link_id=link.id, direction_override=SyncDirection.PUSH
            ),
            uow,
        )

    # The override, not the link's stored PULL, drives the diff and the result.
    assert engine_preview.await_args.args[1] == SyncDirection.PUSH
    assert result.direction == SyncDirection.PUSH
    assert result.tracks_to_add == 2
    assert result.tracks_to_remove == 30
    assert result.tracks_unchanged == 10
    assert result.connector_name == "spotify"
    assert result.playlist_name == "My Playlist"
    assert result.safety_flagged is True
    assert result.safety_message == "Would remove 30 of 40 tracks"
    assert result.safety_removals == 30
    assert result.safety_total == 40
    assert result.safety_remaining == 10
    assert result.confirm_token == "tok-123"


async def test_preview_rejects_link_under_another_playlist():
    """A nested route naming playlist A cannot preview playlist B's link."""
    link = _link()
    uow = make_mock_uow()
    uow.get_playlist_link_repository().get_link = AsyncMock(return_value=link)

    with pytest.raises(NotFoundError, match="not found"):
        await PreviewPlaylistSyncUseCase().execute(
            PreviewPlaylistSyncCommand(
                user_id="u", link_id=link.id, playlist_id=uuid7()
            ),
            uow,
        )
