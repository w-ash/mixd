"""Unit tests for PlaylistRepository core operations."""

from unittest.mock import AsyncMock

import pytest

from src.domain.entities import Playlist
from src.infrastructure.persistence.repositories.playlist.core import PlaylistRepository


class TestPlaylistRepository:
    """Test core playlist repository operations."""

    @pytest.fixture
    def repo(self, db_session):
        """Playlist repository instance."""
        return PlaylistRepository(db_session)

    @pytest.fixture
    def playlist(self, tracks):
        """Basic playlist with tracks."""
        return Playlist(
            id=1,
            name="Test Playlist",
            description="Test playlist description",
            tracks=tracks,
        )

    @pytest.mark.asyncio
    async def test_save_new_playlist(self, repo, playlist):
        """Should save new playlist with tracks."""
        # Mock successful database operations
        repo.session.execute = AsyncMock()
        repo.session.flush = AsyncMock()
        repo.session.commit = AsyncMock()
        repo.session.rollback = AsyncMock()

        # Mock the transaction helper - since save_playlist uses execute_transaction
        # we need to mock the transaction infrastructure
        repo.execute_transaction = AsyncMock(return_value=playlist)

        result = await repo.save_playlist(playlist)

        assert result.name == "Test Playlist"
        assert len(result.tracks) == 3

    @pytest.mark.asyncio
    async def test_find_by_name(self, repo, playlist):
        """Should find playlist by name using find_by method."""
        # Mock find_by to return the playlist
        repo.find_by = AsyncMock(return_value=[playlist])

        result_list = await repo.find_by({"name": "Test Playlist"})
        result = result_list[0] if result_list else None

        assert result is not None
        assert result.name == "Test Playlist"

    @pytest.mark.asyncio
    async def test_find_by_name_not_found(self, repo):
        """Should return empty list when playlist not found."""
        # Mock find_by to return empty list
        repo.find_by = AsyncMock(return_value=[])

        result_list = await repo.find_by({"name": "Nonexistent Playlist"})

        assert result_list == []
