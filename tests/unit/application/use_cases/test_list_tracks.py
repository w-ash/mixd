"""Unit tests for ListTracksUseCase.

Verifies command -> result flow with mocked repositories. The use case
is a thin coordinator — most logic lives in the repository.
"""

import pytest

from src.application.use_cases.list_tracks import (
    ListTracksCommand,
    ListTracksResult,
    ListTracksUseCase,
)
from tests.fixtures import make_tracks
from tests.fixtures.mocks import make_mock_uow


@pytest.fixture
def mock_uow():
    return make_mock_uow()


class TestListTracksUseCase:
    """Happy path and parameter forwarding."""

    async def test_returns_tracks_and_total(self, mock_uow) -> None:
        tracks = make_tracks(3)
        mock_uow.get_track_repository().list_tracks.return_value = (tracks, 3, {1, 3})

        command = ListTracksCommand()
        result = await ListTracksUseCase().execute(command, mock_uow)

        assert isinstance(result, ListTracksResult)
        assert len(result.tracks) == 3
        assert result.total == 3
        assert result.limit == 50
        assert result.offset == 0
        assert result.liked_track_ids == {1, 3}

    async def test_forwards_search_query(self, mock_uow) -> None:
        mock_uow.get_track_repository().list_tracks.return_value = ([], 0, set())

        command = ListTracksCommand(query="radiohead")
        await ListTracksUseCase().execute(command, mock_uow)

        mock_uow.get_track_repository().list_tracks.assert_called_once_with(
            query="radiohead",
            liked=None,
            connector=None,
            sort_by="title_asc",
            limit=50,
            offset=0,
        )

    async def test_forwards_all_filters(self, mock_uow) -> None:
        mock_uow.get_track_repository().list_tracks.return_value = ([], 0, set())

        command = ListTracksCommand(
            query="test",
            liked=True,
            connector="spotify",
            sort_by="duration_desc",
            limit=25,
            offset=50,
        )
        await ListTracksUseCase().execute(command, mock_uow)

        mock_uow.get_track_repository().list_tracks.assert_called_once_with(
            query="test",
            liked=True,
            connector="spotify",
            sort_by="duration_desc",
            limit=25,
            offset=50,
        )

    async def test_empty_result(self, mock_uow) -> None:
        mock_uow.get_track_repository().list_tracks.return_value = ([], 0, set())

        result = await ListTracksUseCase().execute(ListTracksCommand(), mock_uow)

        assert result.tracks == []
        assert result.total == 0
        assert result.liked_track_ids == set()


class TestListTracksCommand:
    """Command defaults and validation."""

    def test_default_values(self) -> None:
        cmd = ListTracksCommand()

        assert cmd.query is None
        assert cmd.liked is None
        assert cmd.connector is None
        assert cmd.sort_by == "title_asc"
        assert cmd.limit == 50
        assert cmd.offset == 0
