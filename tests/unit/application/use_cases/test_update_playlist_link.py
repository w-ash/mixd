"""Unit tests for UpdatePlaylistLinkUseCase."""

from uuid import uuid7

import pytest

from src.application.use_cases.update_playlist_link import (
    UpdatePlaylistLinkCommand,
    UpdatePlaylistLinkUseCase,
)
from src.domain.entities.playlist_link import PlaylistLink, SyncDirection, SyncStatus
from src.domain.exceptions import NotFoundError
from tests.fixtures import make_mock_uow, make_playlist

_PLAYLIST_ID = uuid7()
_LINK_ID = uuid7()


def _make_link(direction=SyncDirection.PUSH) -> PlaylistLink:
    return PlaylistLink(
        id=_LINK_ID,
        playlist_id=_PLAYLIST_ID,
        connector_name="spotify",
        connector_playlist_identifier="ext123",
        sync_direction=direction,
        sync_status=SyncStatus.NEVER_SYNCED,
    )


class TestUpdatePlaylistLinkHappyPath:
    """Successful direction changes."""

    @pytest.mark.asyncio
    async def test_update_direction_push_to_pull(self):
        updated_link = PlaylistLink(
            id=_LINK_ID,
            playlist_id=_PLAYLIST_ID,
            connector_name="spotify",
            connector_playlist_identifier="ext123",
            sync_direction=SyncDirection.PULL,
            sync_status=SyncStatus.NEVER_SYNCED,
        )
        uow = make_mock_uow()
        uow.get_playlist_link_repository().get_link.return_value = _make_link()
        uow.get_playlist_link_repository().update_link_direction.return_value = (
            updated_link
        )
        uow.get_playlist_repository().get_playlist_by_id.return_value = make_playlist(
            id=_PLAYLIST_ID
        )

        result = await UpdatePlaylistLinkUseCase().execute(
            UpdatePlaylistLinkCommand(
                user_id="test-user",
                link_id=_LINK_ID,
                sync_direction=SyncDirection.PULL,
            ),
            uow,
        )

        assert result.link.sync_direction == SyncDirection.PULL
        assert result.link.connector_name == "spotify"
        uow.get_playlist_link_repository().update_link_direction.assert_called_once_with(
            _LINK_ID, SyncDirection.PULL
        )
        uow.commit.assert_called_once()


class TestUpdatePlaylistLinkErrors:
    """Error handling for direction updates."""

    @pytest.mark.asyncio
    async def test_link_not_found_raises(self):
        uow = make_mock_uow()
        uow.get_playlist_link_repository().get_link.return_value = None

        with pytest.raises(NotFoundError, match="not found"):
            await UpdatePlaylistLinkUseCase().execute(
                UpdatePlaylistLinkCommand(
                    user_id="test-user",
                    link_id=uuid7(),
                    sync_direction=SyncDirection.PULL,
                ),
                uow,
            )
