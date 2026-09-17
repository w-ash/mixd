"""Track repository for like operations.

A like is a presence row: ``(user_id, track_id, service)`` exists while the
track is liked on that service. Liking is an upsert, unliking is a delete,
and every read is a presence check.
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, exists, func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_logger
from src.domain.entities import TrackLike
from src.infrastructure.persistence.database.db_models import DBTrackLike
from src.infrastructure.persistence.repositories.base_repo import (
    BaseRepository,
    rows_affected,
)
from src.infrastructure.persistence.repositories.mappers import SimpleMapperFactory
from src.infrastructure.persistence.repositories.repo_decorator import db_operation

logger = get_logger(__name__)

# Use SimpleMapperFactory to eliminate boilerplate - this replaces ~42 lines of repetitive code
TrackLikeMapper = SimpleMapperFactory.create(
    DBTrackLike,
    TrackLike,
)


class TrackLikeRepository(BaseRepository[DBTrackLike, TrackLike]):
    """Repository for track like operations."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize repository with session and mapper."""
        super().__init__(
            session=session,
            model_class=DBTrackLike,
            mapper=TrackLikeMapper(),
        )

    @db_operation("get_track_likes")
    async def get_track_likes(
        self,
        track_id: UUID,
        *,
        user_id: str,
        services: list[str] | None = None,
    ) -> list[TrackLike]:
        """Get likes for a track across services, scoped to user."""
        conditions = [
            self.model_class.track_id == track_id,
            self.model_class.user_id == user_id,
        ]

        if services:
            conditions.append(self.model_class.service.in_(services))

        return await self.find_by(conditions)

    @db_operation("get_liked_status_batch")
    async def get_liked_status_batch(
        self,
        track_ids: list[UUID],
        services: list[str],
        *,
        user_id: str,
    ) -> dict[UUID, set[str]]:
        """Find which of the given services each track is liked on, in 1 query."""
        if not track_ids:
            return {}
        stmt = select(DBTrackLike.track_id, DBTrackLike.service).where(
            DBTrackLike.track_id.in_(track_ids),
            DBTrackLike.service.in_(services),
            DBTrackLike.user_id == user_id,
        )
        rows = (await self.session.execute(stmt)).tuples().all()
        result: dict[UUID, set[str]] = {}
        for track_id, service in rows:
            result.setdefault(track_id, set()).add(service)
        return result

    @db_operation("count_liked_tracks")
    async def count_liked_tracks(
        self,
        service: str,
        *,
        user_id: str,
    ) -> int:
        """Count liked tracks for a service, scoped to user."""
        stmt = self.count([
            self.model_class.service == service,
            self.model_class.user_id == user_id,
        ])
        result = await self.session.execute(stmt)
        return result.scalar_one()

    @db_operation("count_liked_tracks_by_service")
    async def count_liked_tracks_by_service(
        self,
        services: Sequence[str],
        *,
        user_id: str,
    ) -> dict[str, int]:
        """Count likes per service in one grouped query, scoped to user.

        Batch counterpart to ``count_liked_tracks``: N services cost one
        round-trip instead of N. A service with no matching rows is absent
        from the GROUP BY result, so it is filled in as 0.
        """
        if not services:
            return {}

        stmt = (
            select(DBTrackLike.service, func.count())
            .where(
                DBTrackLike.service.in_(services),
                DBTrackLike.user_id == user_id,
            )
            .group_by(DBTrackLike.service)
        )
        result = await self.session.execute(stmt)
        counts = dict(result.tuples().all())
        return {service: counts.get(service, 0) for service in services}

    @db_operation("get_all_liked_tracks")
    async def get_all_liked_tracks(
        self,
        service: str,
        *,
        user_id: str,
        sort_by: str | None = None,
    ) -> list[TrackLike]:
        """Get all tracks liked on a specific service, scoped to user."""
        conditions = [
            self.model_class.service == service,
            self.model_class.user_id == user_id,
        ]

        # Handle special sorting cases that require custom queries
        if sort_by in ["title_asc", "random"]:
            from src.infrastructure.persistence.database.db_models import DBTrack

            stmt = select(self.model_class)
            for condition in conditions:
                stmt = stmt.where(condition)

            if sort_by == "title_asc":
                # Join with tracks table for title sorting
                stmt = stmt.join(DBTrack, self.model_class.track_id == DBTrack.id)
                stmt = stmt.order_by(DBTrack.title)
            elif sort_by == "random":
                stmt = stmt.order_by(func.random())

            result = await self.session.execute(stmt)
            db_models = result.scalars().all()
            return [await self.mapper.to_domain(model) for model in db_models]

        # Use base repository for simple field sorting
        order_by = None
        if sort_by == "liked_at_desc":
            order_by = ("liked_at", False)  # DESC
        elif sort_by == "liked_at_asc":
            order_by = ("liked_at", True)  # ASC

        return await self.find_by(conditions, order_by=order_by)

    @db_operation("get_unsynced_likes")
    async def get_unsynced_likes(
        self,
        source_service: str,
        target_service: str,
        *,
        user_id: str,
        since_timestamp: datetime | None = None,
    ) -> list[TrackLike]:
        """Get tracks liked in source_service with no like in target_service.

        One anti-join: a source row qualifies when no row for the same user
        and track exists on the target service.
        """
        target = DBTrackLike.__table__.alias("target")
        on_target = exists().where(
            target.c.user_id == DBTrackLike.user_id,
            target.c.track_id == DBTrackLike.track_id,
            target.c.service == target_service,
        )
        conditions = [
            DBTrackLike.service == source_service,
            DBTrackLike.user_id == user_id,
            ~on_target,
        ]
        if since_timestamp:
            conditions.append(DBTrackLike.updated_at >= since_timestamp)

        return await self.find_by(conditions)

    @db_operation("save_track_likes_batch")
    async def save_track_likes_batch(
        self,
        likes: list[tuple[UUID, str, datetime | None]],
        *,
        user_id: str,
    ) -> list[TrackLike]:
        """Insert or refresh like rows in bulk.

        Args:
            likes: List of (track_id, service, liked_at) tuples. A ``None``
                ``liked_at`` is stamped with the current time.
            user_id: Owner's user ID.

        Returns:
            List of saved TrackLike domain objects.
        """
        now = datetime.now(UTC)
        entities: list[dict[str, object]] = [
            {
                "user_id": user_id,
                "track_id": track_id,
                "service": service,
                "updated_at": now,
                "liked_at": liked_at or now,
            }
            for track_id, service, liked_at in likes
        ]

        if not entities:
            return []

        return await self.bulk_upsert(
            entities=entities,
            lookup_keys=["user_id", "track_id", "service"],
        )

    @db_operation("delete_track_likes_batch")
    async def delete_track_likes_batch(
        self,
        likes: list[tuple[UUID, str]],
        *,
        user_id: str,
    ) -> int:
        """Delete like rows in bulk; pairs with no row are ignored."""
        if not likes:
            return 0
        stmt = delete(DBTrackLike).where(
            DBTrackLike.user_id == user_id,
            tuple_(DBTrackLike.track_id, DBTrackLike.service).in_(likes),
        )
        return rows_affected(await self.session.execute(stmt))
