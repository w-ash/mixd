"""Track repository for tag operations.

Batch-first: all writes and multi-row reads operate on sequences.
``add_tags`` uses INSERT ... ON CONFLICT DO NOTHING ... RETURNING so only
rows actually inserted come back — the caller writes one event per real
change. Same shape on ``remove_tags``.
"""

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, func, select, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_logger
from src.domain.entities.tag import TagEvent, TrackTag
from src.infrastructure.persistence.database.db_models import (
    DBTrackTag,
    DBTrackTagEvent,
)
from src.infrastructure.persistence.repositories.base_repo import (
    BaseRepository,
    SimpleMapperFactory,
)
from src.infrastructure.persistence.repositories.repo_decorator import db_operation

logger = get_logger(__name__)

TrackTagMapper = SimpleMapperFactory.create(DBTrackTag, TrackTag)


class TrackTagRepository(BaseRepository[DBTrackTag, TrackTag]):
    """Repository for track tag operations."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(
            session=session,
            model_class=DBTrackTag,
            mapper=TrackTagMapper(),
        )

    @db_operation("get_tags")
    async def get_tags(
        self, track_ids: Sequence[UUID], *, user_id: str
    ) -> dict[UUID, list[TrackTag]]:
        """Get tags for a set of tracks. Tracks with no tags are omitted."""
        if not track_ids:
            return {}
        tags = await self.find_by([
            self.model_class.track_id.in_(track_ids),
            self.model_class.user_id == user_id,
        ])
        grouped: dict[UUID, list[TrackTag]] = {}
        for tag in tags:
            grouped.setdefault(tag.track_id, []).append(tag)
        return grouped

    @db_operation("add_tags")
    async def add_tags(
        self, tags: Sequence[TrackTag], *, user_id: str
    ) -> list[TrackTag]:
        """Returns only the tags actually inserted.

        Duplicates (same ``(user_id, track_id, tag)``) are silently skipped
        at the DB level. Callers write one ``TagEvent`` per returned tag.
        """
        if not tags:
            return []

        entities: list[dict[str, object]] = [
            {
                "id": t.id,
                "user_id": user_id,
                "track_id": t.track_id,
                "tag": t.tag,
                "namespace": t.namespace,
                "value": t.value,
                "source": t.source,
                "tagged_at": t.tagged_at,
            }
            for t in tags
        ]
        entities = self._deduplicate_batch(
            entities, ["user_id", "track_id", "tag"], label="add_tags"
        )
        self._add_timestamps(entities)

        async with self.session.begin_nested():
            stmt = (
                pg_insert(self.model_class)
                .values(entities)
                .on_conflict_do_nothing(
                    index_elements=[
                        self.model_class.user_id,
                        self.model_class.track_id,
                        self.model_class.tag,
                    ],
                )
                .returning(self.model_class.id)
            )
            result = await self.session.execute(stmt)
            inserted_ids = {row[0] for row in result.all()}

        return [t for t in tags if t.id in inserted_ids]

    @db_operation("remove_tags")
    async def remove_tags(
        self, pairs: Sequence[tuple[UUID, str]], *, user_id: str
    ) -> list[tuple[UUID, str]]:
        """Returns only the pairs actually removed (missing rows skipped silently)."""
        if not pairs:
            return []
        stmt = (
            delete(self.model_class)
            .where(
                self.model_class.user_id == user_id,
                tuple_(self.model_class.track_id, self.model_class.tag).in_(pairs),
            )
            .returning(self.model_class.track_id, self.model_class.tag)
        )
        result = await self.session.execute(stmt)
        return [(row[0], row[1]) for row in result.all()]

    @db_operation("add_events")
    async def add_events(
        self, events: Sequence[TagEvent], *, user_id: str
    ) -> list[TagEvent]:
        if not events:
            return []
        entities: list[dict[str, object]] = [
            {
                "id": e.id,
                "user_id": user_id,
                "track_id": e.track_id,
                "tag": e.tag,
                "action": e.action,
                "source": e.source,
                "tagged_at": e.tagged_at,
            }
            for e in events
        ]
        self._add_timestamps(entities)
        await self.session.execute(pg_insert(DBTrackTagEvent).values(entities))
        return list(events)

    @db_operation("list_tags")
    async def list_tags(
        self,
        *,
        user_id: str,
        query: str | None = None,
        limit: int = 100,
    ) -> list[tuple[str, int]]:
        """List ``(tag, count)`` sorted by count desc, filtered by trigram ILIKE."""
        count_col = func.count(self.model_class.id)
        stmt = select(self.model_class.tag, count_col).where(
            self.model_class.user_id == user_id
        )
        if query:
            stmt = stmt.where(self.model_class.tag.ilike(f"%{query}%"))
        stmt = (
            stmt.group_by(self.model_class.tag).order_by(count_col.desc()).limit(limit)
        )
        result = await self.session.execute(stmt)
        return [(row[0], row[1]) for row in result.all()]

    @db_operation("count_by_tag")
    async def count_by_tag(self, *, user_id: str) -> dict[str, int]:
        stmt = (
            select(self.model_class.tag, func.count(self.model_class.id))
            .where(self.model_class.user_id == user_id)
            .group_by(self.model_class.tag)
        )
        result = await self.session.execute(stmt)
        return {row[0]: row[1] for row in result.all()}

    @db_operation("list_by_tagged_at")
    async def list_by_tagged_at(
        self,
        *,
        user_id: str,
        before: datetime | None = None,
        after: datetime | None = None,
        limit: int = 50,
    ) -> list[TrackTag]:
        """List tags within a date range, ordered by tagged_at desc."""
        conditions = [self.model_class.user_id == user_id]
        if before is not None:
            conditions.append(self.model_class.tagged_at < before)
        if after is not None:
            conditions.append(self.model_class.tagged_at >= after)
        return await self.find_by(
            conditions,
            order_by=("tagged_at", False),
            limit=limit,
        )
