"""Track repository for like operations."""

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_logger
from src.domain.entities import TrackLike
from src.infrastructure.persistence.database.db_models import DBTrackLike
from src.infrastructure.persistence.repositories.base_repo import (
    BaseRepository,
    SimpleMapperFactory,
)
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
        track_id: int,
        services: list[str] | None = None,
    ) -> list[TrackLike]:
        """Get likes for a track across services."""
        conditions = [self.model_class.track_id == track_id]

        if services:
            conditions.append(self.model_class.service.in_(services))

        return await self.find_by(conditions)

    @db_operation("get_liked_status_batch")
    async def get_liked_status_batch(
        self,
        track_ids: list[int],
        services: list[str],
    ) -> dict[int, dict[str, bool]]:
        """Check like status for multiple tracks across services in 1 query."""
        if not track_ids:
            return {}
        likes = await self.find_by([
            self.model_class.track_id.in_(track_ids),
            self.model_class.service.in_(services),
        ])
        result: dict[int, dict[str, bool]] = {}
        for like in likes:
            result.setdefault(like.track_id, {})[like.service] = like.is_liked
        return result

    @db_operation("get_all_liked_tracks")
    async def get_all_liked_tracks(
        self,
        service: str,
        is_liked: bool = True,
        sort_by: str | None = None,
    ) -> list[TrackLike]:
        """Get all tracks liked on a specific service with optional sorting."""
        conditions = [
            self.model_class.service == service,
            self.model_class.is_liked == is_liked,
        ]

        # Handle special sorting cases that require custom queries
        if sort_by in ["title_asc", "random"]:
            from sqlalchemy import func, select

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
        is_liked: bool = True,
        since_timestamp: datetime | None = None,
    ) -> list[TrackLike]:
        """Get tracks liked in source_service but not in target_service."""
        # First get all source tracks with the requested like status
        source_conditions = [
            self.model_class.service == source_service,
            self.model_class.is_liked == is_liked,
        ]

        if since_timestamp:
            source_conditions.append(self.model_class.updated_at >= since_timestamp)

        source_likes = await self.find_by(source_conditions)

        if not source_likes:
            return []

        # Get track IDs that need syncing
        track_ids = [like.track_id for like in source_likes]

        # Find target likes for these tracks
        target_likes = await self.find_by([
            self.model_class.service == target_service,
            self.model_class.track_id.in_(track_ids),
        ])

        # Create lookup dict of target likes by track_id
        target_likes_dict = {like.track_id: like for like in target_likes}

        # Filter source likes that need syncing to target
        return [
            like
            for like in source_likes
            if like.track_id not in target_likes_dict
            or target_likes_dict[like.track_id].is_liked != is_liked
        ]

    @db_operation("save_track_like")
    async def save_track_like(
        self,
        track_id: int,
        service: str,
        is_liked: bool = True,
        last_synced: datetime | None = None,
        liked_at: datetime | None = None,
    ) -> TrackLike:
        """Save a track like for a service."""
        now = datetime.now(UTC)

        # Prepare new values
        update_values: dict[str, object] = {
            "is_liked": is_liked,
            "updated_at": now,
        }

        if is_liked:
            update_values["liked_at"] = liked_at or now
        else:
            update_values["liked_at"] = None  # Clear on unlike

        if last_synced:
            update_values["last_synced"] = last_synced

        # Use upsert to either create or update
        return await self.upsert(
            lookup_attrs={"track_id": track_id, "service": service},
            create_attrs=update_values,
        )

    @db_operation("save_track_likes_batch")
    async def save_track_likes_batch(
        self,
        likes: list[tuple[int, str, bool, datetime | None, datetime | None]],
    ) -> list[TrackLike]:
        """Save multiple track likes in bulk.

        Args:
            likes: List of (track_id, service, is_liked, last_synced, liked_at) tuples.

        Returns:
            List of saved TrackLike domain objects.
        """
        now = datetime.now(UTC)
        entities: list[dict[str, object]] = []

        for track_id, service, is_liked, last_synced, liked_at in likes:
            entity: dict[str, object] = {
                "track_id": track_id,
                "service": service,
                "is_liked": is_liked,
                "updated_at": now,
                "liked_at": (liked_at or now) if is_liked else None,
            }
            if last_synced:
                entity["last_synced"] = last_synced
            entities.append(entity)

        if not entities:
            return []

        result = await self.bulk_upsert(
            entities=entities,
            lookup_keys=["track_id", "service"],
        )
        # bulk_upsert returns list[TDomainModel] when return_models=True (default)
        return result if isinstance(result, list) else []
