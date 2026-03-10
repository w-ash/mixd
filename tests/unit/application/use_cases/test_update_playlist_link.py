"""Unit tests for UpdatePlaylistLinkUseCase."""

import pytest

from src.application.use_cases.update_playlist_link import (
    UpdatePlaylistLinkCommand,
    UpdatePlaylistLinkUseCase,
)
from src.domain.entities.playlist_link import PlaylistLink, SyncDirection, SyncStatus
from src.domain.exceptions import NotFoundError
from tests.fixtures import make_mock_uow


class TestUpdatePlaylistLinkHappyPath:
    """Successful direction changes."""

    @pytest.mark.asyncio
    async def test_update_direction_push_to_pull(self):
        updated_link = PlaylistLink(
            id=1,
            playlist_id=42,
            connector_name="spotify",
            connector_playlist_identifier="ext123",
            sync_direction=SyncDirection.PULL,
            sync_status=SyncStatus.NEVER_SYNCED,
        )
        uow = make_mock_uow()
        uow.get_playlist_link_repository().update_link_direction.return_value = (
            updated_link
        )

        result = await UpdatePlaylistLinkUseCase().execute(
            UpdatePlaylistLinkCommand(
                link_id=1,
                sync_direction=SyncDirection.PULL,
            ),
            uow,
        )

        assert result.link.sync_direction == SyncDirection.PULL
        assert result.link.connector_name == "spotify"
        uow.get_playlist_link_repository().update_link_direction.assert_called_once_with(
            1, SyncDirection.PULL
        )
        uow.commit.assert_called_once()


class TestUpdatePlaylistLinkErrors:
    """Error handling for direction updates."""

    @pytest.mark.asyncio
    async def test_link_not_found_raises(self):
        uow = make_mock_uow()
        uow.get_playlist_link_repository().update_link_direction.return_value = None

        with pytest.raises(NotFoundError, match="not found"):
            await UpdatePlaylistLinkUseCase().execute(
                UpdatePlaylistLinkCommand(
                    link_id=999,
                    sync_direction=SyncDirection.PULL,
                ),
                uow,
            )
