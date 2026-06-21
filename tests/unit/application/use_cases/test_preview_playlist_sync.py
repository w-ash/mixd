"""Unit tests for PreviewPlaylistSyncUseCase — maps a SyncPlan to the result.

The engine's diff/safety behaviour is tested in
test_playlist_reconciliation_engine; here we verify the read-only mapping of a
SyncPlan into PreviewPlaylistSyncResult (counts, safety flag, direction).
"""

from unittest.mock import AsyncMock, patch
from uuid import uuid7

from src.application.services.playlist_reconciliation_engine import (
    PlaylistReconciliationEngine,
)
from src.application.use_cases.preview_playlist_sync import (
    PreviewPlaylistSyncCommand,
    PreviewPlaylistSyncUseCase,
)
from src.domain.entities.playlist import Playlist
from src.domain.entities.playlist_link import PlaylistLink, SyncDirection
from src.domain.playlist.reconciliation import SyncPlan
from tests.fixtures import make_mock_uow

_RESOLVER = "src.application.use_cases._shared.playlist_resolver.require_playlist_link"


def _link() -> PlaylistLink:
    return PlaylistLink(
        id=uuid7(),
        playlist_id=uuid7(),
        connector_name="spotify",
        connector_playlist_identifier="ext1",
        sync_direction=SyncDirection.PULL,
    )


async def test_preview_maps_plan_to_result():
    link = _link()
    uow = make_mock_uow()
    uow.get_playlist_repository().get_playlist_by_id = AsyncMock(
        return_value=Playlist(name="My Playlist")
    )
    plan = SyncPlan(
        direction=SyncDirection.PULL,
        tracks_to_add=2,
        tracks_to_remove=0,
        tracks_unchanged=3,
        is_noop=False,
    )

    with (
        patch(_RESOLVER, AsyncMock(return_value=link)),
        patch.object(
            PlaylistReconciliationEngine, "preview", AsyncMock(return_value=plan)
        ),
    ):
        result = await PreviewPlaylistSyncUseCase().execute(
            PreviewPlaylistSyncCommand(user_id="u", link_id=link.id), uow
        )

    assert result.tracks_to_add == 2
    assert result.tracks_to_remove == 0
    assert result.tracks_unchanged == 3
    assert result.direction == SyncDirection.PULL
    assert result.connector_name == "spotify"
    assert result.playlist_name == "My Playlist"
    assert result.safety_flagged is False
    assert result.has_comparison_data is True
