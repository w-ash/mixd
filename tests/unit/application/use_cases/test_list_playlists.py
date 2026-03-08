"""Unit tests for ListPlaylistsUseCase.

Tests the simplest use case: listing all playlists with proper
domain entity return types.
"""

import pytest

from src.application.use_cases.list_playlists import (
    ListPlaylistsCommand,
    ListPlaylistsResult,
    ListPlaylistsUseCase,
)
from tests.fixtures import make_playlist
from tests.fixtures.mocks import make_mock_uow


@pytest.fixture
def mock_uow():
    """Mock UnitOfWork with playlist repository."""
    return make_mock_uow()


class TestListPlaylistsUseCase:
    """Test use case execution paths."""

    async def test_returns_all_playlists(self, mock_uow):
        """Test that all playlists from repository are returned."""
        playlists = [
            make_playlist(1, "Playlist A"),
            make_playlist(2, "Playlist B"),
            make_playlist(3, "Playlist C"),
        ]
        mock_uow.get_playlist_repository().list_all_playlists.return_value = playlists

        use_case = ListPlaylistsUseCase()
        result = await use_case.execute(ListPlaylistsCommand(), mock_uow)

        assert isinstance(result, ListPlaylistsResult)
        assert result.total_count == 3
        assert len(result.playlists) == 3
        assert result.has_playlists is True

    async def test_empty_result(self, mock_uow):
        """Test empty playlist list is handled correctly."""
        mock_uow.get_playlist_repository().list_all_playlists.return_value = []

        use_case = ListPlaylistsUseCase()
        result = await use_case.execute(ListPlaylistsCommand(), mock_uow)

        assert result.total_count == 0
        assert result.playlists == []
        assert result.has_playlists is False

    async def test_uses_unit_of_work_context(self, mock_uow):
        """Test that use case enters UoW context manager."""
        mock_uow.get_playlist_repository().list_all_playlists.return_value = []

        use_case = ListPlaylistsUseCase()
        await use_case.execute(ListPlaylistsCommand(), mock_uow)

        mock_uow.__aenter__.assert_called_once()
