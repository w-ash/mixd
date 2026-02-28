"""Unit tests for ListPlaylistsUseCase.

Tests the simplest use case: listing all playlists with proper
domain entity return types.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.application.use_cases.list_playlists import (
    ListPlaylistsResult,
    ListPlaylistsUseCase,
)
from src.domain.entities.playlist import Playlist
from src.domain.entities.track import Artist, Track


def _make_playlist(playlist_id: int, name: str) -> Playlist:
    """Create a test playlist."""
    tracks = [Track(id=1, title="Song", artists=[Artist(name="Artist")])]
    return Playlist.from_tracklist(name=name, tracklist=tracks).with_id(playlist_id)


@pytest.fixture
def mock_uow():
    """Mock UnitOfWork with playlist repository."""
    uow = AsyncMock()
    playlist_repo = AsyncMock()
    uow.get_playlist_repository = MagicMock(return_value=playlist_repo)
    return uow


@pytest.mark.unit
class TestListPlaylistsUseCase:
    """Test use case execution paths."""

    async def test_returns_all_playlists(self, mock_uow):
        """Test that all playlists from repository are returned."""
        playlists = [
            _make_playlist(1, "Playlist A"),
            _make_playlist(2, "Playlist B"),
            _make_playlist(3, "Playlist C"),
        ]
        mock_uow.get_playlist_repository().list_all_playlists.return_value = playlists

        use_case = ListPlaylistsUseCase()
        result = await use_case.execute(mock_uow)

        assert isinstance(result, ListPlaylistsResult)
        assert result.total_count == 3
        assert len(result.playlists) == 3
        assert result.has_playlists is True

    async def test_empty_result(self, mock_uow):
        """Test empty playlist list is handled correctly."""
        mock_uow.get_playlist_repository().list_all_playlists.return_value = []

        use_case = ListPlaylistsUseCase()
        result = await use_case.execute(mock_uow)

        assert result.total_count == 0
        assert result.playlists == []
        assert result.has_playlists is False

    async def test_uses_unit_of_work_context(self, mock_uow):
        """Test that use case enters UoW context manager."""
        mock_uow.get_playlist_repository().list_all_playlists.return_value = []

        use_case = ListPlaylistsUseCase()
        await use_case.execute(mock_uow)

        mock_uow.__aenter__.assert_called_once()
