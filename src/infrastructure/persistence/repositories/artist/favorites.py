"""Artist favorites: presence rows, never a state flag.

The row exists while the artist is favorited and is deleted when it is not, so
``favorite`` is an idempotent upsert and ``unfavorite`` a delete. Both report
whether they changed anything, which is what the UI's optimistic toggle needs
to reconcile against.
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_logger
from src.domain.entities.artist import ArtistFavorite
from src.infrastructure.persistence.database.models import DBArtistFavorite
from src.infrastructure.persistence.repositories.artist.mapper import (
    ArtistFavoriteMapper,
)
from src.infrastructure.persistence.repositories.base_repo import (
    BaseRepository,
    rows_affected,
)
from src.infrastructure.persistence.repositories.repo_decorator import db_operation

logger = get_logger(__name__)


class ArtistFavoriteRepository(BaseRepository[DBArtistFavorite, ArtistFavorite]):
    """Repository for the artists a user favorited."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize with database session and data mapper."""
        super().__init__(
            session=session,
            model_class=DBArtistFavorite,
            mapper=ArtistFavoriteMapper(),
        )

    @db_operation("favorite_artist")
    async def favorite(self, artist_id: UUID, *, user_id: str) -> bool:
        """Favorite an artist. True when the row was created.

        ``DO NOTHING`` rather than ``DO UPDATE``: re-favoriting is not a new
        decision, and rewriting ``favorited_at`` would lose when the user
        actually made it.
        """
        now = datetime.now(UTC)
        stmt = (
            pg_insert(DBArtistFavorite)
            .values(
                user_id=user_id,
                artist_id=artist_id,
                favorited_at=now,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_nothing(index_elements=["user_id", "artist_id"])
            .returning(DBArtistFavorite.artist_id)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none() is not None

    @db_operation("unfavorite_artist")
    async def unfavorite(self, artist_id: UUID, *, user_id: str) -> bool:
        """Unfavorite an artist. True when a row was deleted."""
        result = await self.session.execute(
            delete(DBArtistFavorite).where(
                DBArtistFavorite.artist_id == artist_id,
                DBArtistFavorite.user_id == user_id,
            )
        )
        return rows_affected(result) > 0

    @db_operation("get_favorite_status_batch")
    async def get_favorite_status_batch(
        self, artist_ids: Sequence[UUID], *, user_id: str
    ) -> set[UUID]:
        """Which of the given artists are favorited — one query for a page."""
        if not artist_ids:
            return set()
        result = await self.session.execute(
            select(DBArtistFavorite.artist_id).where(
                DBArtistFavorite.artist_id.in_(artist_ids),
                DBArtistFavorite.user_id == user_id,
            )
        )
        return set(result.scalars().all())

    @db_operation("get_favorite_artist_ids")
    async def get_favorite_artist_ids(self, *, user_id: str) -> frozenset[UUID]:
        """Every favorited artist id, for filters that need the whole set."""
        result = await self.session.execute(
            select(DBArtistFavorite.artist_id).where(
                DBArtistFavorite.user_id == user_id
            )
        )
        return frozenset(result.scalars().all())


__all__ = ["ArtistFavoriteRepository"]
