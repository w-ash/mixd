"""Unit tests for ListTracksUseCase.

Verifies command -> result flow with mocked repositories. The use case
is a thin coordinator — most logic lives in the repository.
"""

import pytest

from src.application.pagination import decode_cursor
from src.application.use_cases.list_tracks import (
    ListTracksCommand,
    ListTracksUseCase,
)
from src.domain.repositories.track import PlayFilters, TrackListingPage
from tests.fixtures import make_tracks
from tests.fixtures.mocks import make_mock_uow


def _page(
    tracks=(),
    total=0,
    liked_track_ids=frozenset(),
    next_page_key=None,
    facets=None,
) -> TrackListingPage:
    """Build a TrackListingPage dict for mock return values."""
    return TrackListingPage(
        tracks=list(tracks),
        total=total,
        liked_track_ids=set(liked_track_ids),
        next_page_key=next_page_key,
        facets=facets,
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
            next_page_key=("Track 3", tracks[2].id),
        )

        command = ListTracksCommand(user_id="test-user")
        result = await ListTracksUseCase().execute(command, mock_uow)

        assert result.tracks == tracks
        assert result.total == 3
        assert result.limit == 50
        assert result.offset == 0
        assert result.liked_track_ids == {1, 3}
        # The page's last row becomes the seek point, under the active sort.
        assert result.next_cursor is not None
        decoded = decode_cursor(result.next_cursor)
        assert decoded.sort_key == "last_played_desc"
        assert decoded.sort_value == "Track 3"
        assert decoded.last_id == tracks[2].id

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
            artist_id=None,
            play_filters=PlayFilters(),
            sort_by="duration_desc",
            limit=25,
            offset=50,
            after_value=None,
            after_id=None,
            include_total=True,
            include_facets=False,
        )

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
            PageCursor(sort_key="title_asc", sort_value="Radiohead", last_id=test_id)
        )
        mock_uow.get_track_repository().list_tracks.return_value = _page()

        command = ListTracksCommand(
            user_id="test-user", cursor=cursor, sort_by="title_asc"
        )
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
            PageCursor(sort_key="title_asc", sort_value="Test", last_id=uuid7())
        )
        mock_uow.get_track_repository().list_tracks.return_value = _page()

        command = ListTracksCommand(
            user_id="test-user", cursor=cursor, sort_by="duration_asc"
        )
        await ListTracksUseCase().execute(command, mock_uow)

        call_kwargs = mock_uow.get_track_repository().list_tracks.call_args.kwargs
        assert call_kwargs["after_value"] is None
        assert call_kwargs["after_id"] is None

    async def test_cursor_from_opposite_direction_is_refused(self, mock_uow) -> None:
        """A ``title_asc`` cursor under ``title_desc`` shares the column but would
        seek from the wrong end — the key mismatch refuses it."""
        from uuid import uuid7

        from src.application.pagination import PageCursor, encode_cursor

        cursor = encode_cursor(
            PageCursor(sort_key="title_asc", sort_value="Test", last_id=uuid7())
        )
        mock_uow.get_track_repository().list_tracks.return_value = _page()

        command = ListTracksCommand(
            user_id="test-user", cursor=cursor, sort_by="title_desc"
        )
        await ListTracksUseCase().execute(command, mock_uow)

        call_kwargs = mock_uow.get_track_repository().list_tracks.call_args.kwargs
        assert call_kwargs["after_value"] is None
        assert call_kwargs["after_id"] is None
        assert call_kwargs["include_total"] is True

    async def test_total_none_when_cursor_present(self, mock_uow) -> None:
        """When a cursor is used, include_total=False and total=None is propagated."""
        from uuid import uuid7

        from src.application.pagination import PageCursor, encode_cursor

        cursor = encode_cursor(
            PageCursor(sort_key="title_asc", sort_value="Test", last_id=uuid7())
        )
        mock_uow.get_track_repository().list_tracks.return_value = _page(
            total=None,  # Repository returns None when include_total=False
        )

        command = ListTracksCommand(
            user_id="test-user", cursor=cursor, sort_by="title_asc"
        )
        result = await ListTracksUseCase().execute(command, mock_uow)

        call_kwargs = mock_uow.get_track_repository().list_tracks.call_args.kwargs
        assert call_kwargs["include_total"] is False
        assert result.total is None


class TestListTracksCommandTagNormalization:
    """Tags are normalized on the Command so every caller (CLI + web) matches
    stored, normalized tags — not just the web handler."""

    def test_normalizes_case_and_whitespace(self) -> None:
        cmd = ListTracksCommand(user_id="u", tags=["Mood:Chill", "  ENERGY:High "])
        assert cmd.tags == ("mood:chill", "energy:high")

    def test_none_stays_none(self) -> None:
        assert ListTracksCommand(user_id="u", tags=None).tags is None

    def test_invalid_tag_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="invalid characters"):
            ListTracksCommand(user_id="u", tags=["cafe!"])
