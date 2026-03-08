"""Unit tests for GetTrackPlaylistsUseCase.

Tests that the use case delegates existence check + playlist lookup
to the appropriate repositories via UoW.
"""

from unittest.mock import AsyncMock

import pytest

from src.application.use_cases.get_track_playlists import (
    GetTrackPlaylistsCommand,
    GetTrackPlaylistsUseCase,
)
from src.domain.exceptions import NotFoundError
from tests.fixtures import make_mock_uow, make_playlist


class TestGetTrackPlaylistsUseCase:
    """GetTrackPlaylistsUseCase returns playlists containing a track."""

    async def test_returns_playlists_for_track(self) -> None:
        playlists = [
            make_playlist(id=1, name="My Mix"),
            make_playlist(id=2, name="Chill"),
        ]
        track_repo = AsyncMock()
        playlist_repo = AsyncMock()
        playlist_repo.get_playlists_for_track.return_value = playlists
        uow = make_mock_uow(track_repo=track_repo, playlist_repo=playlist_repo)

        result = await GetTrackPlaylistsUseCase().execute(
            GetTrackPlaylistsCommand(track_id=42), uow
        )

        assert len(result.playlists) == 2
        assert result.playlists[0].name == "My Mix"
        track_repo.get_by_id.assert_called_once_with(42)
        playlist_repo.get_playlists_for_track.assert_called_once_with(42)

    async def test_returns_empty_list_when_no_playlists(self) -> None:
        track_repo = AsyncMock()
        playlist_repo = AsyncMock()
        playlist_repo.get_playlists_for_track.return_value = []
        uow = make_mock_uow(track_repo=track_repo, playlist_repo=playlist_repo)

        result = await GetTrackPlaylistsUseCase().execute(
            GetTrackPlaylistsCommand(track_id=42), uow
        )

        assert result.playlists == []

    async def test_raises_not_found_for_missing_track(self) -> None:
        track_repo = AsyncMock()
        track_repo.get_by_id.side_effect = NotFoundError("Track 999 not found")
        uow = make_mock_uow(track_repo=track_repo)

        with pytest.raises(NotFoundError, match="999"):
            await GetTrackPlaylistsUseCase().execute(
                GetTrackPlaylistsCommand(track_id=999), uow
            )
