"""Unit tests for ListPlaylistLinksUseCase."""

import pytest

from src.application.use_cases.list_playlist_links import (
    ListPlaylistLinksCommand,
    ListPlaylistLinksUseCase,
)
from src.domain.entities.playlist_link import PlaylistLink, SyncDirection, SyncStatus
from tests.fixtures import make_mock_uow


class TestListPlaylistLinksHappyPath:
    """Listing links for a playlist."""

    @pytest.mark.asyncio
    async def test_returns_links_for_playlist(self):
        link = PlaylistLink(
            id=1,
            playlist_id=42,
            connector_name="spotify",
            connector_playlist_identifier="abc123",
            sync_direction=SyncDirection.PUSH,
            sync_status=SyncStatus.SYNCED,
        )
        uow = make_mock_uow()
        uow.get_playlist_link_repository().get_links_for_playlist.return_value = [link]

        result = await ListPlaylistLinksUseCase().execute(
            ListPlaylistLinksCommand(playlist_id=42), uow
        )

        assert len(result.links) == 1
        assert result.links[0].connector_name == "spotify"
        assert result.links[0].connector_playlist_identifier == "abc123"

    @pytest.mark.asyncio
    async def test_returns_empty_for_unlinked_playlist(self):
        uow = make_mock_uow()
        uow.get_playlist_link_repository().get_links_for_playlist.return_value = []

        result = await ListPlaylistLinksUseCase().execute(
            ListPlaylistLinksCommand(playlist_id=99), uow
        )

        assert result.links == []
