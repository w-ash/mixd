"""Unit tests for FavoriteArtistUseCase — both directions plus idempotency."""

from uuid import uuid7

import pytest

from src.application.use_cases.favorite_artist import (
    FavoriteArtistCommand,
    FavoriteArtistUseCase,
)
from src.domain.exceptions import NotFoundError
from tests.fixtures import make_artist
from tests.fixtures.mocks import make_mock_uow


@pytest.fixture
def mock_uow():
    uow = make_mock_uow()
    uow.get_artist_repository().get_artist_by_id.return_value = make_artist("Burial")
    return uow


class TestFavoriteArtistUseCase:
    """Favorite, unfavorite, repeats and the unknown artist."""

    async def test_favorites_an_artist(self, mock_uow) -> None:
        artist_id = uuid7()
        mock_uow.get_artist_favorite_repository().favorite.return_value = True

        result = await FavoriteArtistUseCase().execute(
            FavoriteArtistCommand(
                user_id="test-user", artist_id=artist_id, is_favorited=True
            ),
            mock_uow,
        )

        assert result.artist_id == artist_id
        assert result.is_favorited is True
        assert result.changed is True
        mock_uow.get_artist_favorite_repository().favorite.assert_awaited_once_with(
            artist_id, user_id="test-user"
        )
        mock_uow.commit.assert_awaited_once()

    async def test_repeat_favorite_reports_no_change(self, mock_uow) -> None:
        mock_uow.get_artist_favorite_repository().favorite.return_value = False

        result = await FavoriteArtistUseCase().execute(
            FavoriteArtistCommand(
                user_id="test-user", artist_id=uuid7(), is_favorited=True
            ),
            mock_uow,
        )

        assert result.is_favorited is True
        assert result.changed is False

    async def test_unfavorites_an_artist(self, mock_uow) -> None:
        artist_id = uuid7()
        mock_uow.get_artist_favorite_repository().unfavorite.return_value = True

        result = await FavoriteArtistUseCase().execute(
            FavoriteArtistCommand(
                user_id="test-user", artist_id=artist_id, is_favorited=False
            ),
            mock_uow,
        )

        assert result.is_favorited is False
        assert result.changed is True
        mock_uow.get_artist_favorite_repository().unfavorite.assert_awaited_once_with(
            artist_id, user_id="test-user"
        )

    async def test_repeat_unfavorite_reports_no_change(self, mock_uow) -> None:
        mock_uow.get_artist_favorite_repository().unfavorite.return_value = False

        result = await FavoriteArtistUseCase().execute(
            FavoriteArtistCommand(
                user_id="test-user", artist_id=uuid7(), is_favorited=False
            ),
            mock_uow,
        )

        assert result.changed is False

    async def test_unknown_artist_raises_not_found(self, mock_uow) -> None:
        mock_uow.get_artist_repository().get_artist_by_id.return_value = None

        with pytest.raises(NotFoundError):
            await FavoriteArtistUseCase().execute(
                FavoriteArtistCommand(
                    user_id="test-user", artist_id=uuid7(), is_favorited=True
                ),
                mock_uow,
            )

        mock_uow.get_artist_favorite_repository().favorite.assert_not_awaited()
