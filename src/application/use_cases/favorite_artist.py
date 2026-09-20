"""Use case for favoriting and unfavoriting an artist.

One use case for both directions, like ``SetTrackPreferenceUseCase``: the
Command carries the target state, so the API's POST and DELETE, the CLI's two
commands and the chat write tool all reach the same code path.

Favorites are presence rows, so a repeat call is a no-op rather than an error —
``changed`` reports whether a row actually moved, which is what lets the caller
say "already favorited" instead of lying about a write.
"""

from uuid import UUID

from attrs import define

from src.domain.exceptions import NotFoundError
from src.domain.repositories.uow import UnitOfWorkProtocol


@define(frozen=True, slots=True)
class FavoriteArtistCommand:
    """Set one artist's favorite state."""

    user_id: str
    artist_id: UUID
    is_favorited: bool


@define(frozen=True, slots=True)
class FavoriteArtistResult:
    """The state after the write, and whether it changed anything."""

    artist_id: UUID
    is_favorited: bool
    changed: bool


@define(slots=True)
class FavoriteArtistUseCase:
    """Favorite or unfavorite an artist, idempotently."""

    async def execute(
        self, command: FavoriteArtistCommand, uow: UnitOfWorkProtocol
    ) -> FavoriteArtistResult:
        """Write the favorite state.

        The artist is verified first: favoriting a nonexistent id would insert a
        row the list page can never show, and the row's FK would fail later
        rather than here where the caller can act on it.

        Raises:
            NotFoundError: The artist does not exist, or belongs to another user.
        """
        async with uow:
            artist = await uow.get_artist_repository().get_artist_by_id(
                command.artist_id, user_id=command.user_id
            )
            if artist is None:
                raise NotFoundError(f"Artist {command.artist_id} not found")

            favorites = uow.get_artist_favorite_repository()
            if command.is_favorited:
                changed = await favorites.favorite(
                    command.artist_id, user_id=command.user_id
                )
            else:
                changed = await favorites.unfavorite(
                    command.artist_id, user_id=command.user_id
                )
            await uow.commit()

            return FavoriteArtistResult(
                artist_id=command.artist_id,
                is_favorited=command.is_favorited,
                changed=changed,
            )


async def run_favorite_artist(
    *, user_id: str, artist_id: UUID, is_favorited: bool
) -> FavoriteArtistResult:
    """Set an artist's favorite state via ``execute_use_case`` — the CLI's entry."""
    from src.application.runner import execute_use_case

    command = FavoriteArtistCommand(
        user_id=user_id, artist_id=artist_id, is_favorited=is_favorited
    )
    return await execute_use_case(
        lambda uow: FavoriteArtistUseCase().execute(command, uow),
        user_id=user_id,
    )
