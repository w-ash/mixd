"""Track repository for tag operations.

Batch-first: all writes and multi-row reads operate on sequences.
``add_tags`` uses INSERT ... ON CONFLICT DO NOTHING ... RETURNING so only
rows actually inserted come back — the caller writes one event per real
change. Same shape on ``remove_tags``.
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid7

from sqlalchemy import CursorResult, delete, func, select, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_logger
from src.domain.entities.sourced_metadata import MetadataSource
from src.domain.entities.tag import TagEvent, TrackTag, normalize_tag, parse_tag
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
        self,
        pairs: Sequence[tuple[UUID, str]],
        *,
        user_id: str,
        source: MetadataSource | None = None,
    ) -> list[tuple[UUID, str]]:
        """Returns only the pairs actually removed (missing rows skipped silently)."""
        if not pairs:
            return []
        where_clauses = [
            self.model_class.user_id == user_id,
            tuple_(self.model_class.track_id, self.model_class.tag).in_(pairs),
        ]
        if source is not None:
            where_clauses.append(self.model_class.source == source)
        stmt = (
            delete(self.model_class)
            .where(*where_clauses)
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
    ) -> list[tuple[str, int, datetime]]:
        """List ``(tag, count, last_used_at)`` sorted by count desc, filtered by trigram ILIKE."""
        count_col = func.count(self.model_class.id)
        last_used_col = func.max(self.model_class.tagged_at)
        stmt = select(self.model_class.tag, count_col, last_used_col).where(
            self.model_class.user_id == user_id
        )
        if query:
            stmt = stmt.where(self.model_class.tag.ilike(f"%{query}%"))
        stmt = (
            stmt.group_by(self.model_class.tag).order_by(count_col.desc()).limit(limit)
        )
        result = await self.session.execute(stmt)
        return [(row[0], row[1], row[2]) for row in result.all()]

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

    @db_operation("rename_tag")
    async def rename_tag(self, *, user_id: str, source: str, target: str) -> int:
        """Rename ``source`` → ``target`` for one user; idempotent on existing target.

        Three-step atomic operation: (1) read source rows so provenance can
        carry to the new rows, (2) bulk-INSERT target rows with ON CONFLICT
        DO NOTHING (preserves the existing target where it was already
        present), (3) DELETE source rows. Wraps in a savepoint so any DB
        error rolls back the whole operation.

        Writes ``remove(source)`` events for every affected track plus
        ``add(target)`` events only for tracks that didn't already carry
        ``target`` — the audit log reflects real state changes.
        """
        normalized_source = normalize_tag(source)
        normalized_target = normalize_tag(target)
        if normalized_source == normalized_target:
            return 0

        src_stmt = select(self.model_class).where(
            self.model_class.user_id == user_id,
            self.model_class.tag == normalized_source,
        )
        src_rows = (await self.session.scalars(src_stmt)).all()
        if not src_rows:
            return 0

        target_namespace, target_value = parse_tag(normalized_target)
        target_entities: list[dict[str, object]] = [
            {
                "id": uuid7(),
                "user_id": user_id,
                "track_id": row.track_id,
                "tag": normalized_target,
                "namespace": target_namespace,
                "value": target_value,
                "source": row.source,
                "tagged_at": row.tagged_at,
            }
            for row in src_rows
        ]
        self._add_timestamps(target_entities)

        async with self.session.begin_nested():
            insert_result = await self.session.execute(
                pg_insert(self.model_class)
                .values(target_entities)
                .on_conflict_do_nothing(
                    index_elements=[
                        self.model_class.user_id,
                        self.model_class.track_id,
                        self.model_class.tag,
                    ],
                )
                .returning(self.model_class.track_id)
            )
            newly_added_track_ids = {row[0] for row in insert_result.all()}

            del_stmt = delete(self.model_class).where(
                self.model_class.user_id == user_id,
                self.model_class.tag == normalized_source,
            )
            # session.execute() returns Result[Any] in stubs but a DELETE
            # produces a CursorResult at runtime — same cast pattern used
            # by base_repo.bulk_insert_ignore_conflicts.
            del_result = cast(CursorResult[Any], await self.session.execute(del_stmt))  # pyright: ignore[reportExplicitAny]  # CursorResult is generic over row tuple shape
            affected = del_result.rowcount or 0

        now = datetime.now(UTC)
        events: list[TagEvent] = []
        for row in src_rows:
            events.append(
                TagEvent(
                    user_id=user_id,
                    track_id=row.track_id,
                    tag=normalized_source,
                    action="remove",
                    source="manual",
                    tagged_at=now,
                )
            )
            if row.track_id in newly_added_track_ids:
                events.append(
                    TagEvent(
                        user_id=user_id,
                        track_id=row.track_id,
                        tag=normalized_target,
                        action="add",
                        source="manual",
                        tagged_at=now,
                    )
                )
        await self.add_events(events, user_id=user_id)

        return affected

    @db_operation("merge_tags")
    async def merge_tags(self, *, user_id: str, source: str, target: str) -> int:
        """Merge ``source`` into ``target`` — alias for :meth:`rename_tag`.

        Same operation; separate name lets the API expose distinct
        rename / merge endpoints when the UX intent differs.
        """
        return await self.rename_tag(user_id=user_id, source=source, target=target)

    @db_operation("delete_tag")
    async def delete_tag(self, *, user_id: str, tag: str) -> int:
        """Bulk-delete ``tag`` from a user; cascades to the event log.

        Per the v0.7.6 product decision: tag-event rows for the deleted
        tag are also removed because the audit trail's subject no longer
        exists. No remove events are written. Returns the number of
        ``track_tags`` rows deleted.
        """
        normalized = normalize_tag(tag)
        async with self.session.begin_nested():
            await self.session.execute(
                delete(DBTrackTagEvent).where(
                    DBTrackTagEvent.user_id == user_id,
                    DBTrackTagEvent.tag == normalized,
                )
            )
            del_result = cast(
                CursorResult[Any],  # pyright: ignore[reportExplicitAny]  # CursorResult is generic over row tuple shape
                await self.session.execute(
                    delete(self.model_class).where(
                        self.model_class.user_id == user_id,
                        self.model_class.tag == normalized,
                    )
                ),
            )
        return del_result.rowcount or 0
