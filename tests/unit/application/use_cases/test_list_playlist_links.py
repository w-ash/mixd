"""Unit tests for ListPlaylistLinksUseCase."""

from uuid import uuid7

import pytest

from src.application.use_cases.list_playlist_links import (
    ListPlaylistLinksCommand,
    ListPlaylistLinksUseCase,
)
from src.domain.entities.playlist_link import PlaylistLink, SyncDirection, SyncStatus
from src.domain.exceptions import NotFoundError
from tests.fixtures import make_mock_uow, make_playlist

_PLAYLIST_ID = uuid7()


class TestListPlaylistLinksHappyPath:
    """Listing links for a playlist."""

    @pytest.mark.asyncio
    async def test_returns_links_for_playlist(self):
        link = PlaylistLink(
            id=uuid7(),
            playlist_id=_PLAYLIST_ID,
            connector_name="spotify",
            connector_playlist_identifier="abc123",
            sync_direction=SyncDirection.PUSH,
            sync_status=SyncStatus.SYNCED,
        )
        uow = make_mock_uow()
        uow.get_playlist_link_repository().get_links_for_playlist.return_value = [link]
        uow.get_playlist_repository().get_playlist_by_id.return_value = make_playlist(
            id=_PLAYLIST_ID
        )

        result = await ListPlaylistLinksUseCase().execute(
            ListPlaylistLinksCommand(user_id="test-user", playlist_id=_PLAYLIST_ID), uow
        )

        assert len(result.links) == 1
        assert result.links[0].connector_name == "spotify"
        assert result.links[0].connector_playlist_identifier == "abc123"

    @pytest.mark.asyncio
    async def test_another_users_playlist_is_not_found(self):
        """Links are listed only after the ownership gate passes."""
        uow = make_mock_uow()
        uow.get_playlist_repository().is_owned_by.return_value = False

        with pytest.raises(NotFoundError):
            await ListPlaylistLinksUseCase().execute(
                ListPlaylistLinksCommand(user_id="test-user", playlist_id=uuid7()), uow
            )

        uow.get_playlist_link_repository().get_links_for_playlist.assert_not_called()
