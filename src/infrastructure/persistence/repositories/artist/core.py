"""Canonical artist persistence: save, look up, list, enrich, delete.

Mirrors ``track/core.py``. The listing is the interesting part: two of its
sorts order by something ``artists`` does not store — a count over
``track_artists`` and a timestamp on ``artist_favorites`` — so the repository
resolves those declared names to correlated subqueries and hands the
expression to the shared keyset machinery. Everything else about the page
(probe row, cursor, NULL tail) is the same code the track listing pages with.

Every query names ``user_id`` in its WHERE clause: RLS is inert in production
(PDR-002), so repository scoping *is* the tenant isolation.
"""

from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Final, cast
from uuid import UUID

from sqlalchemy import ColumnElement, Select, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_logger
from src.domain.entities.artist import Artist, ArtistKind
from src.domain.entities.shared import SortKey
from src.domain.repositories.artist import (
    ARTIST_SORTS,
    DEFAULT_ARTIST_SORT,
    ArtistListingPage,
    ArtistSortBy,
)
from src.infrastructure.persistence.database.models import (
    DBArtist,
    DBArtistFavorite,
    DBArtistMapping,
    DBTrackArtist,
)
from src.infrastructure.persistence.repositories.artist.mapper import ArtistMapper
from src.infrastructure.persistence.repositories.base_repo import BaseRepository
from src.infrastructure.persistence.repositories.repo_decorator import db_operation

logger = get_logger(__name__)


def _track_count_column(user_id: str) -> ColumnElement[object]:
    """``track_count`` as a correlated subquery over the user's credits.

    Distinct on ``track_id``: a track that credits the same artist twice (a
    remix crediting the original act again) is one track, not two.
    """
    # The cast is the SQLAlchemy-generic surface: ``ColumnElement`` is
    # invariant in its value type, so a ``ScalarSelect[int]`` cannot be handed
    # to a parameter that accepts any sort column without it.
    return cast(
        "ColumnElement[object]",
        select(func.count(func.distinct(DBTrackArtist.track_id)))
        .where(
            DBTrackArtist.artist_id == DBArtist.id,
            DBTrackArtist.user_id == user_id,
        )
        .correlate(DBArtist)
        .scalar_subquery(),
    )


def _favorited_at_column(user_id: str) -> ColumnElement[object]:
    """``favorited_at`` as a correlated subquery; NULL when not favorited."""
    return cast(
        "ColumnElement[object]",
        select(DBArtistFavorite.favorited_at)
        .where(
            DBArtistFavorite.artist_id == DBArtist.id,
            DBArtistFavorite.user_id == user_id,
        )
        .correlate(DBArtist)
        .scalar_subquery(),
    )


# The sort columns that are not columns. Keys must cover
# ``COMPUTED_ARTIST_SORT_COLUMNS``; the keyset unit suite asserts that they do,
# so a new computed sort cannot be declared without an expression to order by.
COMPUTED_SORT_COLUMNS: Final[Mapping[str, Callable[[str], ColumnElement[object]]]] = {
    "track_count": _track_count_column,
    "favorited_at": _favorited_at_column,
}


def connector_names_stmt(
    artist_ids: Sequence[UUID], user_id: str
) -> Select[tuple[UUID, str]]:
    """The one query behind "which services is this artist mapped to?".

    Shared by the listing's side map and the connector repository's batch
    accessor so both read mappings the same way.
    """
    return (
        select(DBArtistMapping.artist_id, DBArtistMapping.connector_name)
        .where(
            DBArtistMapping.artist_id.in_(artist_ids),
            DBArtistMapping.user_id == user_id,
        )
        .distinct()
    )


class ArtistRepository(BaseRepository[DBArtist, Artist]):
    """Repository for canonical artist operations."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize repository with session and mapper."""
        super().__init__(
            session=session,
            model_class=DBArtist,
            mapper=ArtistMapper(),
        )

    # ── reads ────────────────────────────────────────────────────────

    @db_operation("get_artist_by_id")
    async def get_artist_by_id(self, artist_id: UUID, *, user_id: str) -> Artist | None:
        """Get one artist, scoped to the user."""
        db_artist = await self._execute_query_one(
            self.select_by_id(artist_id).where(DBArtist.user_id == user_id)
        )
        return None if db_artist is None else await self.mapper.to_domain(db_artist)

    @db_operation("list_needing_enrichment")
    async def list_needing_enrichment(
        self,
        *,
        user_id: str,
        older_than: datetime | None = None,
        limit: int = 100,
    ) -> list[Artist]:
        """Artists with no MBID, or last touched before ``older_than``."""
        stale: ColumnElement[bool] = (
            DBArtist.mbid.is_(None)
            if older_than is None
            else or_(DBArtist.mbid.is_(None), DBArtist.updated_at < older_than)
        )
        stmt = (
            select(DBArtist)
            .where(DBArtist.user_id == user_id, stale)
            .order_by(DBArtist.updated_at.asc().nullsfirst(), DBArtist.id.asc())
            .limit(limit)
        )
        return [
            await self.mapper.to_domain(row) for row in await self._execute_query(stmt)
        ]

    @db_operation("count_tracks_by_artist")
    async def count_tracks_by_artist(
        self, artist_ids: Sequence[UUID], *, user_id: str
    ) -> dict[UUID, int]:
        """Count distinct credited tracks per artist in one grouped query."""
        if not artist_ids:
            return {}
        result = await self.session.execute(
            select(
                DBTrackArtist.artist_id,
                func.count(func.distinct(DBTrackArtist.track_id)),
            )
            .where(
                DBTrackArtist.artist_id.in_(artist_ids),
                DBTrackArtist.user_id == user_id,
            )
            .group_by(DBTrackArtist.artist_id)
        )
        counts = {
            artist_id: count
            for artist_id, count in result.tuples()
            if artist_id is not None
        }
        # An artist with no credits is a zero, not an absence: callers render
        # the number straight from this map.
        return {artist_id: counts.get(artist_id, 0) for artist_id in artist_ids}

    # ── listing ──────────────────────────────────────────────────────

    @db_operation("list_artists")
    async def list_artists(
        self,
        *,
        user_id: str,
        query: str | None = None,
        favorites_only: bool = False,
        sort_by: ArtistSortBy = DEFAULT_ARTIST_SORT,
        limit: int = 50,
        offset: int = 0,
        after_value: SortKey | None = None,
        after_id: UUID | None = None,
        include_total: bool = True,
    ) -> ArtistListingPage:
        """List artists with search, favorites filter, sorting and pagination."""
        conditions = self._build_list_filters(
            user_id=user_id, query=query, favorites_only=favorites_only
        )

        total: int | None = None
        if include_total:
            count_stmt = select(func.count()).select_from(DBArtist).where(*conditions)
            total = (await self.session.execute(count_stmt)).scalar_one()
            if total == 0:
                return ArtistListingPage(
                    artists=[],
                    total=0,
                    track_counts={},
                    favorited_ids=set(),
                    connector_names={},
                    next_page_key=None,
                )

        sort = ARTIST_SORTS[sort_by]
        computed = COMPUTED_SORT_COLUMNS.get(sort.column)
        page, has_more = await self._fetch_page_rows(
            select(DBArtist).where(*conditions),
            sort=sort,
            limit=limit,
            offset=offset,
            after_value=after_value,
            after_id=after_id,
            column=None if computed is None else computed(user_id),
        )

        artists = [await self.mapper.to_domain(row) for row in page]
        artist_ids = [artist.id for artist in artists]
        track_counts = await self.count_tracks_by_artist(artist_ids, user_id=user_id)
        favorited_at = await self._favorited_at_batch(artist_ids, user_id=user_id)
        connector_names = await self._connector_names(artist_ids, user_id=user_id)

        next_page_key: tuple[object, UUID] | None = None
        if has_more:
            last = page[-1]
            next_page_key = (
                self._sort_value(
                    last,
                    sort.column,
                    track_counts=track_counts,
                    favorited_at=favorited_at,
                ),
                last.id,
            )

        return ArtistListingPage(
            artists=artists,
            total=total,
            track_counts=track_counts,
            favorited_ids=set(favorited_at),
            connector_names=connector_names,
            next_page_key=next_page_key,
        )

    def _build_list_filters(
        self, *, user_id: str, query: str | None, favorites_only: bool
    ) -> list[ColumnElement[bool]]:
        """WHERE conditions for :meth:`list_artists` — always user-scoped."""
        conditions: list[ColumnElement[bool]] = [DBArtist.user_id == user_id]
        if query:
            # pg_trgm GIN accelerates the substring ILIKE (migration 060).
            conditions.append(DBArtist.name.ilike(f"%{query}%"))
        if favorites_only:
            conditions.append(
                select(DBArtistFavorite.artist_id)
                .where(
                    DBArtistFavorite.artist_id == DBArtist.id,
                    DBArtistFavorite.user_id == user_id,
                )
                .correlate(DBArtist)
                .exists()
            )
        return conditions

    @staticmethod
    def _sort_value(
        row: DBArtist,
        column: str,
        *,
        track_counts: Mapping[UUID, int],
        favorited_at: Mapping[UUID, datetime | None],
    ) -> object:
        """The cursor value for ``row`` under a stored or computed sort column.

        A computed sort reads its value from the side map the page already
        built, so ordering by it costs no extra query.
        """
        if column == "track_count":
            return track_counts.get(row.id, 0)
        if column == "favorited_at":
            return favorited_at.get(row.id)
        return getattr(row, column)  # pyright: ignore[reportAny]  # SQLAlchemy column reflection

    async def _favorited_at_batch(
        self, artist_ids: Sequence[UUID], *, user_id: str
    ) -> dict[UUID, datetime | None]:
        """When each of these artists was favorited; absent means not favorited.

        Presence is the favorite, so a key with a ``None`` value is still a
        favorite — it just has no timestamp to sort by.
        """
        if not artist_ids:
            return {}
        result = await self.session.execute(
            select(DBArtistFavorite.artist_id, DBArtistFavorite.favorited_at).where(
                DBArtistFavorite.artist_id.in_(artist_ids),
                DBArtistFavorite.user_id == user_id,
            )
        )
        # ``.all()`` first: a SQLAlchemy result has ``keys()``, so ``dict``
        # would read the result itself as a mapping and subscript it.
        return dict(result.tuples().all())

    async def _connector_names(
        self, artist_ids: Sequence[UUID], *, user_id: str
    ) -> dict[UUID, list[str]]:
        """Which services each artist is mapped to, as sorted name lists."""
        if not artist_ids:
            return {}
        result = await self.session.execute(connector_names_stmt(artist_ids, user_id))
        names: dict[UUID, list[str]] = {}
        for artist_id, connector_name in result.tuples():
            names.setdefault(artist_id, []).append(connector_name)
        return {artist_id: sorted(found) for artist_id, found in names.items()}

    # ── writes ───────────────────────────────────────────────────────

    @db_operation("save_artists")
    async def save_artists(self, artists: Sequence[Artist]) -> list[Artist]:
        """Insert a batch of new canonical artists in one statement."""
        if not artists:
            return []

        now = datetime.now(UTC)
        rows: list[dict[str, object]] = [
            {
                # The entity minted the id, so the caller already knows it and
                # RETURNING only has to put the rows back in input order.
                "id": artist.id,
                "user_id": artist.user_id,
                "name": artist.name,
                "mbid": artist.mbid,
                "kind": artist.kind,
                "created_at": now,
                "updated_at": now,
            }
            for artist in artists
        ]
        result = await self.session.execute(
            pg_insert(DBArtist).values(rows).returning(DBArtist)
        )
        saved = {row.id: row for row in result.scalars().all()}
        return [await self.mapper.to_domain(saved[artist.id]) for artist in artists]

    @db_operation("set_identity")
    async def set_identity(
        self,
        artist_id: UUID,
        *,
        user_id: str,
        mbid: str | None,
        kind: ArtistKind | None,
    ) -> None:
        """Write the MusicBrainz anchor and entity kind, touching ``updated_at``."""
        await self.session.execute(
            update(DBArtist)
            .where(DBArtist.id == artist_id, DBArtist.user_id == user_id)
            .values(mbid=mbid, kind=kind, updated_at=datetime.now(UTC))
        )

    @db_operation("touch")
    async def touch(self, artist_ids: Sequence[UUID], *, user_id: str) -> None:
        """Move ``updated_at`` on a batch without changing anything else."""
        if not artist_ids:
            return
        await self.session.execute(
            update(DBArtist)
            .where(DBArtist.id.in_(artist_ids), DBArtist.user_id == user_id)
            .values(updated_at=datetime.now(UTC))
        )
