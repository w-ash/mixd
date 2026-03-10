"""Unit tests for DeletePlaylistLinkUseCase."""

import pytest

from src.application.use_cases.delete_playlist_link import (
    DeletePlaylistLinkCommand,
    DeletePlaylistLinkUseCase,
)
from src.domain.exceptions import NotFoundError
from tests.fixtures import make_mock_uow


class TestDeletePlaylistLinkHappyPath:
    """Deleting an existing playlist link."""

    @pytest.mark.asyncio
    async def test_deletes_existing_link(self):
        uow = make_mock_uow()
        uow.get_playlist_link_repository().delete_link.return_value = True

        result = await DeletePlaylistLinkUseCase().execute(
            DeletePlaylistLinkCommand(link_id=1), uow
        )

        assert result.deleted is True
        uow.get_playlist_link_repository().delete_link.assert_called_once_with(1)


class TestDeletePlaylistLinkErrors:
    """Error cases for deleting playlist links."""

    @pytest.mark.asyncio
    async def test_not_found_raises(self):
        uow = make_mock_uow()
        uow.get_playlist_link_repository().delete_link.return_value = False

        with pytest.raises(NotFoundError, match="not found"):
            await DeletePlaylistLinkUseCase().execute(
                DeletePlaylistLinkCommand(link_id=999), uow
            )
