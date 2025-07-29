"""Track repository for like operations."""

from datetime import UTC, datetime

from attrs import define
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_logger
from src.domain.entities import TrackLike
from src.infrastructure.persistence.database.db_models import DBTrackLike
from src.infrastructure.persistence.repositories.base_repo import (
    BaseModelMapper,
    BaseRepository,
)
from src.infrastructure.persistence.repositories.repo_decorator import db_operation

logger = get_logger(__name__)


@define(frozen=True, slots=True)
class TrackLikeMapper(BaseModelMapper[DBTrackLike, TrackLike]):
    """Maps between DBTrackLike and TrackLike domain models."""

    @staticmethod
    def get_default_relationships() -> list[str]:
        """Return relationships to eagerly load for track likes.

        Following SQLAlchemy 2.0 best practices, we specify which relationships
        should be eagerly loaded. For TrackLike, we typically don't need to load
        the related track object since we're usually just working with the like
        status itself. If needed, consumers can explicitly request track loading.
        """
        return []  # Don't eagerly load track by default for performance

    @staticmethod
    async def to_domain(db_model: DBTrackLike) -> TrackLike:
        """Convert database like to domain model."""
        if not db_model:
            return None

        return TrackLike(
            track_id=db_model.track_id,
            service=db_model.service,
            is_liked=db_model.is_liked,
            liked_at=db_model.liked_at,
            last_synced=db_model.last_synced,
            id=db_model.id,
        )

    @staticmethod
    def to_db(domain_model: TrackLike) -> DBTrackLike:
        """Convert domain like to database model."""
        return DBTrackLike(
            id=domain_model.id,
            track_id=domain_model.track_id,
            service=domain_model.service,
            is_liked=domain_model.is_liked,
            liked_at=domain_model.liked_at,
            last_synced=domain_model.last_synced,
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
            order_by = ("liked_at", True)   # ASC
        
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
    ) -> TrackLike:
        """Save a track like for a service."""
        now = datetime.now(UTC)

        # Prepare new values
        update_values = {
            "is_liked": is_liked,
            "updated_at": now,
        }

        if is_liked:
            update_values["liked_at"] = now

        if last_synced:
            update_values["last_synced"] = last_synced

        # Use upsert to either create or update
        return await self.upsert(
            lookup_attrs={"track_id": track_id, "service": service},
            create_attrs=update_values,
        )

    @db_operation("delete_track_like")
    async def delete_track_like(
        self,
        track_id: int,
        service: str,
    ) -> bool:
        """Remove a track like status for a service."""
        # Find the like
        like = await self.find_one_by([
            self.model_class.track_id == track_id,
            self.model_class.service == service,
        ])

        if not like:
            return False

        # Use soft_delete from base repository
        try:
            if like.id is None:
                return False
            await self.soft_delete(like.id)
            return True
        except ValueError:
            return False
