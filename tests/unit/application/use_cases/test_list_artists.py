"""Unit tests for ListArtistsUseCase.

Command -> result flow with a mocked repository: the use case forwards filters,
encodes the next-page cursor, and falls back to the default sort on garbage.
"""

from uuid import uuid7

import pytest

from src.application.pagination import PageCursor, encode_cursor
from src.application.use_cases.list_artists import (
    ListArtistsCommand,
    ListArtistsUseCase,
)
from src.domain.repositories.artist import ARTIST_SORTS, ArtistListingPage
from tests.fixtures import make_artist
from tests.fixtures.mocks import make_mock_uow


def _page(
    artists=(),
    total=0,
    track_counts=None,
    favorited_ids=frozenset(),
    connector_names=None,
    next_page_key=None,
) -> ArtistListingPage:
    return ArtistListingPage(
        artists=list(artists),
        total=total,
        track_counts=dict(track_counts or {}),
        favorited_ids=set(favorited_ids),
        connector_names=dict(connector_names or {}),
        next_page_key=next_page_key,
    )


@pytest.fixture
def mock_uow():
    return make_mock_uow()


class TestListArtistsUseCase:
    """Happy path, filter forwarding, cursors and sort fallback."""

    async def test_returns_artists_with_side_maps(self, mock_uow) -> None:
        artist = make_artist("Caribou")
        mock_uow.get_artist_repository().list_artists.return_value = _page(
            artists=[artist],
            total=1,
            track_counts={artist.id: 12},
            favorited_ids={artist.id},
            connector_names={artist.id: ["spotify", "lastfm"]},
        )

        result = await ListArtistsUseCase().execute(
            ListArtistsCommand(user_id="test-user"), mock_uow
        )

        assert [a.name for a in result.artists] == ["Caribou"]
        assert result.total == 1
        assert result.track_counts[artist.id] == 12
        assert artist.id in result.favorited_ids
        assert result.connector_names[artist.id] == ["spotify", "lastfm"]
        assert result.next_cursor is None

    async def test_forwards_filters(self, mock_uow) -> None:
        repo = mock_uow.get_artist_repository()
        repo.list_artists.return_value = _page()

        await ListArtistsUseCase().execute(
            ListArtistsCommand(
                user_id="test-user",
                search="cari",
                favorites_only=True,
                sort_by="track_count_desc",
                limit=25,
                offset=50,
            ),
            mock_uow,
        )

        repo.list_artists.assert_called_once_with(
            user_id="test-user",
            query="cari",
            favorites_only=True,
            sort_by="track_count_desc",
            limit=25,
            offset=50,
            after_value=None,
            after_id=None,
            include_total=True,
        )

    async def test_encodes_next_cursor(self, mock_uow) -> None:
        artist = make_artist("Daphni")
        mock_uow.get_artist_repository().list_artists.return_value = _page(
            artists=[artist], next_page_key=("Daphni", artist.id)
        )

        result = await ListArtistsUseCase().execute(
            ListArtistsCommand(user_id="test-user"), mock_uow
        )

        assert result.next_cursor is not None

    async def test_cursor_seeks_and_skips_the_count(self, mock_uow) -> None:
        repo = mock_uow.get_artist_repository()
        repo.list_artists.return_value = _page()
        last_id = uuid7()
        cursor = encode_cursor(
            PageCursor(
                sort_key=ARTIST_SORTS["name_asc"].key,
                sort_value="Caribou",
                last_id=last_id,
            )
        )

        await ListArtistsUseCase().execute(
            ListArtistsCommand(user_id="test-user", cursor=cursor), mock_uow
        )

        kwargs = repo.list_artists.call_args.kwargs
        assert kwargs["after_value"] == "Caribou"
        assert kwargs["after_id"] == last_id
        assert kwargs["include_total"] is False

    async def test_cursor_from_another_sort_falls_back_to_page_one(
        self, mock_uow
    ) -> None:
        repo = mock_uow.get_artist_repository()
        repo.list_artists.return_value = _page()
        cursor = encode_cursor(
            PageCursor(sort_key="name_asc", sort_value="X", last_id=uuid7())
        )

        await ListArtistsUseCase().execute(
            ListArtistsCommand(
                user_id="test-user", cursor=cursor, sort_by="track_count_desc"
            ),
            mock_uow,
        )

        kwargs = repo.list_artists.call_args.kwargs
        assert kwargs["after_id"] is None
        assert kwargs["include_total"] is True

    async def test_invalid_cursor_falls_back_to_offset(self, mock_uow) -> None:
        repo = mock_uow.get_artist_repository()
        repo.list_artists.return_value = _page()

        await ListArtistsUseCase().execute(
            ListArtistsCommand(user_id="test-user", cursor="not-a-cursor"), mock_uow
        )

        assert repo.list_artists.call_args.kwargs["after_id"] is None

    def test_unknown_sort_takes_the_default(self) -> None:
        command = ListArtistsCommand(user_id="test-user", sort_by="nonsense")

        assert command.sort_by == "name_asc"
