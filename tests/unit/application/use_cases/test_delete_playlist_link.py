"""Unit tests for DeletePlaylistLinkUseCase."""

from uuid import uuid7

import pytest

from src.application.use_cases.delete_playlist_link import (
    DeletePlaylistLinkCommand,
    DeletePlaylistLinkUseCase,
)
from src.domain.entities.playlist_link import PlaylistLink, SyncDirection, SyncStatus
from src.domain.exceptions import NotFoundError
from tests.fixtures import make_mock_uow, make_playlist

_PLAYLIST_ID = uuid7()
_LINK_ID = uuid7()


def _make_link() -> PlaylistLink:
    return PlaylistLink(
        id=_LINK_ID,
        playlist_id=_PLAYLIST_ID,
        connector_name="spotify",
        connector_playlist_identifier="ext123",
        sync_direction=SyncDirection.PUSH,
        sync_status=SyncStatus.NEVER_SYNCED,
    )


class TestDeletePlaylistLinkHappyPath:
    """Deleting an existing playlist link."""

    @pytest.mark.asyncio
    async def test_deletes_existing_link(self):
        uow = make_mock_uow()
        uow.get_playlist_link_repository().get_link.return_value = _make_link()
        uow.get_playlist_link_repository().delete_link.return_value = True
        uow.get_playlist_repository().get_playlist_by_id.return_value = make_playlist(
            id=_PLAYLIST_ID
        )

        result = await DeletePlaylistLinkUseCase().execute(
            DeletePlaylistLinkCommand(user_id="test-user", link_id=_LINK_ID), uow
        )

        assert result.deleted is True
        uow.get_playlist_link_repository().delete_link.assert_called_once_with(_LINK_ID)


class TestDeletePlaylistLinkErrors:
    """Error cases for deleting playlist links."""

    @pytest.mark.asyncio
    async def test_not_found_raises(self):
        uow = make_mock_uow()
        uow.get_playlist_link_repository().get_link.return_value = None

        with pytest.raises(NotFoundError, match="not found"):
            await DeletePlaylistLinkUseCase().execute(
                DeletePlaylistLinkCommand(user_id="test-user", link_id=uuid7()), uow
            )
