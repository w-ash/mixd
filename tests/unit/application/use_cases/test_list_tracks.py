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
from src.domain.repositories.interfaces import TrackListingPage
from tests.fixtures import make_tracks
from tests.fixtures.mocks import make_mock_uow


def _page(
    tracks=(),
    total=0,
    liked_track_ids=frozenset(),
    next_page_key=None,
) -> TrackListingPage:
    """Build a TrackListingPage dict for mock return values."""
    return TrackListingPage(
        tracks=list(tracks),
        total=total,
        liked_track_ids=set(liked_track_ids),
        next_page_key=next_page_key,
    )


@pytest.fixture
def mock_uow():
    return make_mock_uow()


class TestListTracksUseCase:
    """Happy path and parameter forwarding."""

    async def test_returns_tracks_and_total(self, mock_uow) -> None:
        tracks = make_tracks(3)
        mock_uow.get_track_repository().list_tracks.return_value = _page(
            tracks=tracks,
            total=3,
            liked_track_ids={1, 3},
            next_page_key=("Track 3", 3),
        )

        command = ListTracksCommand(user_id="test-user")
        result = await ListTracksUseCase().execute(command, mock_uow)

        assert isinstance(result, ListTracksResult)
        assert len(result.tracks) == 3
        assert result.total == 3
        assert result.limit == 50
        assert result.offset == 0
        assert result.liked_track_ids == {1, 3}
        assert result.next_cursor is not None

    async def test_forwards_search_query(self, mock_uow) -> None:
        mock_uow.get_track_repository().list_tracks.return_value = _page()

        command = ListTracksCommand(user_id="test-user", query="radiohead")
        await ListTracksUseCase().execute(command, mock_uow)

        mock_uow.get_track_repository().list_tracks.assert_called_once_with(
            user_id="test-user",
            query="radiohead",
            liked=None,
            connector=None,
            preference=None,
            tags=None,
            tag_mode="and",
            namespace=None,
            sort_by="title_asc",
            limit=50,
            offset=0,
            after_value=None,
            after_id=None,
            include_total=True,
        )

    async def test_forwards_all_filters(self, mock_uow) -> None:
        mock_uow.get_track_repository().list_tracks.return_value = _page()

        command = ListTracksCommand(
            user_id="test-user",
            query="test",
            liked=True,
            connector="spotify",
            sort_by="duration_desc",
            limit=25,
            offset=50,
        )
        await ListTracksUseCase().execute(command, mock_uow)

        mock_uow.get_track_repository().list_tracks.assert_called_once_with(
            user_id="test-user",
            query="test",
            liked=True,
            connector="spotify",
            preference=None,
            tags=None,
            tag_mode="and",
            namespace=None,
            sort_by="duration_desc",
            limit=25,
            offset=50,
            after_value=None,
            after_id=None,
            include_total=True,
        )

    async def test_empty_result(self, mock_uow) -> None:
        mock_uow.get_track_repository().list_tracks.return_value = _page()

        result = await ListTracksUseCase().execute(
            ListTracksCommand(user_id="test-user"), mock_uow
        )

        assert result.tracks == []
        assert result.total == 0
        assert result.liked_track_ids == set()
        assert result.next_cursor is None

    async def test_last_page_has_no_next_cursor(self, mock_uow) -> None:
        tracks = make_tracks(2)
        mock_uow.get_track_repository().list_tracks.return_value = _page(
            tracks=tracks, total=2
        )

        result = await ListTracksUseCase().execute(
            ListTracksCommand(user_id="test-user"), mock_uow
        )

        assert len(result.tracks) == 2
        assert result.next_cursor is None


class TestListTracksCursorPagination:
    """Cursor encoding/decoding through the use case."""

    async def test_valid_cursor_decoded_and_forwarded(self, mock_uow) -> None:
        from uuid import uuid7

        from src.application.pagination import PageCursor, encode_cursor

        test_id = uuid7()
        cursor = encode_cursor(
            PageCursor(sort_column="title", sort_value="Radiohead", last_id=test_id)
        )
        mock_uow.get_track_repository().list_tracks.return_value = _page()

        command = ListTracksCommand(user_id="test-user", cursor=cursor)
        await ListTracksUseCase().execute(command, mock_uow)

        call_kwargs = mock_uow.get_track_repository().list_tracks.call_args.kwargs
        assert call_kwargs["after_value"] == "Radiohead"
        assert call_kwargs["after_id"] == test_id
        assert call_kwargs["include_total"] is False

    async def test_invalid_cursor_falls_back_to_offset(self, mock_uow) -> None:
        mock_uow.get_track_repository().list_tracks.return_value = _page()

        command = ListTracksCommand(
            user_id="test-user", cursor="not-valid-base64!!!", offset=100
        )
        await ListTracksUseCase().execute(command, mock_uow)

        call_kwargs = mock_uow.get_track_repository().list_tracks.call_args.kwargs
        assert call_kwargs["after_value"] is None
        assert call_kwargs["after_id"] is None
        assert call_kwargs["offset"] == 100
        assert call_kwargs["include_total"] is True

    async def test_cursor_sort_mismatch_falls_back_to_offset(self, mock_uow) -> None:
        from uuid import uuid7

        from src.application.pagination import PageCursor, encode_cursor

        # Cursor was built for title sort, but command uses duration sort
        cursor = encode_cursor(
            PageCursor(sort_column="title", sort_value="Test", last_id=uuid7())
        )
        mock_uow.get_track_repository().list_tracks.return_value = _page()

        command = ListTracksCommand(
            user_id="test-user", cursor=cursor, sort_by="duration_asc"
        )
        await ListTracksUseCase().execute(command, mock_uow)

        call_kwargs = mock_uow.get_track_repository().list_tracks.call_args.kwargs
        assert call_kwargs["after_value"] is None
        assert call_kwargs["after_id"] is None

    async def test_total_none_when_cursor_present(self, mock_uow) -> None:
        """When a cursor is used, include_total=False and total=None is propagated."""
        from uuid import uuid7

        from src.application.pagination import PageCursor, encode_cursor

        cursor = encode_cursor(
            PageCursor(sort_column="title", sort_value="Test", last_id=uuid7())
        )
        mock_uow.get_track_repository().list_tracks.return_value = _page(
            total=None,  # Repository returns None when include_total=False
        )

        command = ListTracksCommand(user_id="test-user", cursor=cursor)
        result = await ListTracksUseCase().execute(command, mock_uow)

        assert result.total is None


class TestListTracksCommand:
    """Command defaults and validation."""

    def test_default_values(self) -> None:
        cmd = ListTracksCommand(user_id="test-user")

        assert cmd.query is None
        assert cmd.liked is None
        assert cmd.connector is None
        assert cmd.sort_by == "title_asc"
        assert cmd.limit == 50
        assert cmd.offset == 0
        assert cmd.cursor is None
