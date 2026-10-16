"""Canonical artist persistence: save, look up, list, enrich, delete.

Mirrors ``track/core.py``. The listing is the interesting part: two of its
sorts order by something ``artists`` does not store — a count over
``track_artists`` and a timestamp on ``artist_favorites`` — so the repository
attaches each one to the listing statement. The count is one grouped
aggregate over the user's credits, outer-joined onto ``artists``; the
timestamp is a correlated subquery. The listing selects its sort column, stored
or computed, beside each row: one expression orders the page, seeks it and
gives the cursor value. The track-count sort also reads the page's
``track_counts`` from that column. Every other sort counts only the page's
artists, so the default listing does not aggregate the whole library.
Everything else about the page (probe row, NULL tail) is the same code the
track listing pages with.

Every query names ``user_id`` in its WHERE clause: RLS is inert in production
(PDR-002), so repository scoping *is* the tenant isolation.
"""

from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Final, cast
from uuid import UUID

from sqlalchemy import ColumnElement, Select, Subquery, func, or_, select, update
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


def _track_counts_subquery(user_id: str) -> Subquery:
    """Distinct credited tracks per artist, as ``(artist_id, n)`` rows.

    One grouped aggregate over the user's credits, which
    ``ix_track_artists_user_artist`` feeds. Distinct on ``track_id``: a track
    that credits the same artist twice (a remix crediting the original act
    again) has two credit rows, one per position, and is one track, not two.
    An artist with no credits has no row here.
    """
    return (
        select(
            DBTrackArtist.artist_id,
            func.count(func.distinct(DBTrackArtist.track_id)).label("n"),
        )
        .where(DBTrackArtist.user_id == user_id)
        .group_by(DBTrackArtist.artist_id)
        .subquery()
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


type _ArtistSelect = Select[tuple[DBArtist]]

# Attaches a sort column that ``artists`` does not store. It takes the listing
# statement and the user id. It gives the statement with what the column needs
# joined, and the column that the page orders by, seeks by and selects for its
# cursor value.
type _AttachSortColumn = Callable[
    [_ArtistSelect, str], tuple[_ArtistSelect, ColumnElement[object]]
]


def _attach_track_count(
    stmt: _ArtistSelect, user_id: str
) -> tuple[_ArtistSelect, ColumnElement[object]]:
    """Outer-join the credit aggregate and give the track-count column.

    An artist with no credits has no aggregate row, and counts zero.
    """
    counts = _track_counts_subquery(user_id)
    track_count: ColumnElement[object] = func.coalesce(counts.c.n, 0)
    return stmt.outerjoin(counts, counts.c.artist_id == DBArtist.id), track_count


# One entry per sort the domain declares ``computed``; the keyset unit suite
# asserts the two agree, so a computed sort cannot be declared without a way
# to attach its column to the listing. The favorite timestamp is a correlated
# subquery, so its statement needs no join.
COMPUTED_SORT_COLUMNS: Final[Mapping[str, _AttachSortColumn]] = {
    "track_count": _attach_track_count,
    "favorited_at": lambda stmt, user_id: (stmt, _favorited_at_column(user_id)),
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
        aggregate = _track_counts_subquery(user_id)
        artist_col: ColumnElement[UUID] = aggregate.c.artist_id
        count_col: ColumnElement[int] = aggregate.c.n
        result = await self.session.execute(
            select(artist_col, count_col).where(artist_col.in_(artist_ids))
        )
        counts = dict(result.tuples().all())
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
        stmt = select(DBArtist).where(*conditions)
        if sort.computed:
            stmt, sort_col = COMPUTED_SORT_COLUMNS[sort.column](stmt, user_id)
        else:
            sort_col = self.sort_column(sort)
        paged: Select[tuple[DBArtist, object]] = stmt.add_columns(sort_col)
        rows, has_more = await self._fetch_page_tuples(
            paged,
            sort=sort,
            limit=limit,
            offset=offset,
            after_value=after_value,
            after_id=after_id,
            column=sort_col,
        )

        artists = [await self.mapper.to_domain(row) for row, _ in rows]
        artist_ids = [artist.id for artist in artists]
        # On the track-count sort, the joined aggregate already holds each
        # row's count. Any other sort counts only this page's artists.
        track_counts = (
            # The selected column is the count aggregate on this sort.
            {row.id: cast("int", value) for row, value in rows}
            if sort.column == "track_count"
            else await self.count_tracks_by_artist(artist_ids, user_id=user_id)
        )
        favorited_at = await self._favorited_at_batch(artist_ids, user_id=user_id)
        connector_names = await self._connector_names(artist_ids, user_id=user_id)

        next_page_key = (rows[-1][1], rows[-1][0].id) if has_more else None

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
