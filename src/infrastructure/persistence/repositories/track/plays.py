"""Track repository for play operations."""

from datetime import datetime
from typing import Any

from attrs import define
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from toolz import groupby

from src.config import get_logger
from src.domain.entities import TrackPlay
from src.infrastructure.persistence.database.db_models import DBTrackPlay
from src.infrastructure.persistence.repositories.base_repo import (
    BaseModelMapper,
    BaseRepository,
)
from src.infrastructure.persistence.repositories.repo_decorator import db_operation

logger = get_logger(__name__)


@define(frozen=True, slots=True)
class TrackPlayMapper(BaseModelMapper[DBTrackPlay, TrackPlay]):
    """Maps between DBTrackPlay and TrackPlay domain models."""

    @staticmethod
    def get_default_relationships() -> list[str]:
        """Return relationships to eagerly load for track plays."""
        return []  # Don't eagerly load track by default for performance

    @staticmethod
    async def to_domain(db_model: DBTrackPlay) -> TrackPlay:
        """Convert database play to domain model."""
        return TrackPlay(
            track_id=db_model.track_id,
            service=db_model.service,
            played_at=db_model.played_at,
            ms_played=db_model.ms_played,
            context=db_model.context,
            id=db_model.id,
            import_timestamp=db_model.import_timestamp,
            import_source=db_model.import_source,
            import_batch_id=db_model.import_batch_id,
        )

    @staticmethod
    def to_db(domain_model: TrackPlay) -> DBTrackPlay:
        """Convert domain play to database model."""
        return DBTrackPlay(
            track_id=domain_model.track_id,
            service=domain_model.service,
            played_at=domain_model.played_at,
            ms_played=domain_model.ms_played,
            context=domain_model.context,
            import_timestamp=domain_model.import_timestamp,
            import_source=domain_model.import_source,
            import_batch_id=domain_model.import_batch_id,
        )


class TrackPlayRepository(BaseRepository[DBTrackPlay, TrackPlay]):
    """Repository for track play operations."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize repository with session and mapper."""
        super().__init__(
            session=session,
            model_class=DBTrackPlay,
            mapper=TrackPlayMapper(),
        )

    @db_operation("bulk_insert_plays")
    async def bulk_insert_plays(self, plays: list[TrackPlay]) -> int:
        """Bulk insert track plays efficiently."""
        if not plays:
            return 0

        # Filter out plays with NULL track_id to prevent constraint violations
        valid_plays = [play for play in plays if play.track_id is not None]

        if not valid_plays:
            logger.warning(
                f"Filtered out all {len(plays)} plays due to NULL track_id - no plays to insert"
            )
            return 0

        if len(valid_plays) < len(plays):
            logger.warning(
                f"Filtered out {len(plays) - len(valid_plays)} plays with NULL track_id (kept {len(valid_plays)} valid plays)"
            )

        play_data = [
            {
                "track_id": play.track_id,
                "service": play.service,
                "played_at": play.played_at,
                "ms_played": play.ms_played,
                "context": play.context,
                "import_timestamp": play.import_timestamp,
                "import_source": play.import_source,
                "import_batch_id": play.import_batch_id,
            }
            for play in valid_plays
        ]

        result = await self.bulk_upsert(
            play_data,
            lookup_keys=["track_id", "service", "played_at", "ms_played"],
            return_models=False,
        )

        # Return count of inserted records
        return len(play_data) if isinstance(result, list) else result

    @db_operation("get_plays_by_batch")
    async def get_plays_by_batch(self, import_batch_id: str) -> list[TrackPlay]:
        """Get all plays from a specific import batch."""
        return await self.find_by([
            self.model_class.import_batch_id == import_batch_id,
            self.model_class.is_deleted == False,  # noqa: E712
        ])

    @db_operation("get_play_aggregations")
    async def get_play_aggregations(
        self,
        track_ids: list[int],
        metrics: list[str],
        period_start: datetime | None = None,
        period_end: datetime | None = None,
    ) -> dict[str, dict[int, Any]]:
        """Get aggregated play data for specified tracks and metrics.

        Args:
            track_ids: List of track IDs to get play data for
            metrics: List of metrics to calculate ["total_plays", "last_played_dates", "period_plays"]
            period_start: Start date for period-based metrics (optional)
            period_end: End date for period-based metrics (optional)

        Returns:
            Dictionary mapping metric names to {track_id: value} dictionaries
        """
        if not track_ids or not metrics:
            logger.debug(
                "Empty track_ids or metrics provided",
                track_count=len(track_ids),
                metric_count=len(metrics),
            )
            return {}

        logger.debug(
            "Getting play aggregations",
            track_count=len(track_ids),
            metrics=metrics,
            period_start=period_start,
            period_end=period_end,
        )

        result = {}

        # Build base query
        base_query = select(DBTrackPlay).where(
            DBTrackPlay.track_id.in_(track_ids),
            DBTrackPlay.is_deleted == False,  # noqa: E712
        )

        # Execute query to get all relevant plays
        query_result = await self.session.execute(base_query)
        plays = query_result.scalars().all()

        # Use toolz for efficient aggregation
        plays_by_track = groupby(lambda p: p.track_id, plays)

        # Calculate total plays if requested
        if "total_plays" in metrics:
            result["total_plays"] = {
                track_id: len(track_plays)
                for track_id, track_plays in plays_by_track.items()
            }

        # Calculate last played dates if requested
        if "last_played_dates" in metrics:

            def get_last_played(track_plays):
                if not track_plays:
                    return None
                return max(play.played_at for play in track_plays)

            result["last_played_dates"] = {
                track_id: get_last_played(track_plays)
                for track_id, track_plays in plays_by_track.items()
            }

        # Calculate period plays if requested
        if "period_plays" in metrics and period_start and period_end:

            def count_period_plays(track_plays):
                return len([
                    play
                    for play in track_plays
                    if period_start <= play.played_at <= period_end
                ])

            result["period_plays"] = {
                track_id: count_period_plays(track_plays)
                for track_id, track_plays in plays_by_track.items()
            }

        # Ensure all requested track_ids are present in results
        for metric_name in result:
            for track_id in track_ids:
                if track_id not in result[metric_name]:
                    if metric_name == "total_plays" or metric_name == "period_plays":
                        result[metric_name][track_id] = 0
                    else:  # last_played_dates
                        result[metric_name][track_id] = None

        return result

    @db_operation("get_total_play_counts")
    async def get_total_play_counts(self, track_ids: list[int]) -> dict[int, int]:
        """Get total play counts for specified tracks."""
        aggregations = await self.get_play_aggregations(track_ids, ["total_plays"])
        return aggregations.get("total_plays", {})

    @db_operation("get_last_played_dates")
    async def get_last_played_dates(
        self, track_ids: list[int]
    ) -> dict[int, datetime | None]:
        """Get last played dates for specified tracks."""
        aggregations = await self.get_play_aggregations(
            track_ids, ["last_played_dates"]
        )
        return aggregations.get("last_played_dates", {})

    @db_operation("get_period_play_counts")
    async def get_period_play_counts(
        self, track_ids: list[int], start_date: datetime, end_date: datetime
    ) -> dict[int, int]:
        """Get play counts within a specific time period."""
        aggregations = await self.get_play_aggregations(
            track_ids, ["period_plays"], start_date, end_date
        )
        return aggregations.get("period_plays", {})

    @db_operation("get_recent_plays")
    async def get_recent_plays(self, limit: int = 100, sort_by: str | None = None) -> list[TrackPlay]:
        """Get recent plays with optional sorting."""
        # Handle special sorting cases that require custom queries or aggregations
        if sort_by in ["total_plays_desc", "last_played_desc", "title_asc", "random"]:
            from sqlalchemy import func, select
            from src.infrastructure.persistence.database.db_models import DBTrack
            
            if sort_by == "total_plays_desc":
                # Get plays grouped by track_id, ordered by count
                stmt = (
                    select(self.model_class.track_id, func.count().label("play_count"))
                    .where(self.model_class.is_deleted == False)  # noqa: E712
                    .group_by(self.model_class.track_id)
                    .order_by(func.count().desc())
                    .limit(limit)
                )
                
                result = await self.session.execute(stmt)
                track_counts = result.fetchall()
                
                # Get the most recent play for each of these tracks
                if not track_counts:
                    return []
                    
                track_ids = [row[0] for row in track_counts]
                
                # Get one recent play per track, maintaining the order
                plays = []
                for track_id in track_ids:
                    recent_play_stmt = (
                        select(self.model_class)
                        .where(
                            self.model_class.track_id == track_id,
                            self.model_class.is_deleted == False  # noqa: E712
                        )
                        .order_by(self.model_class.played_at.desc())
                        .limit(1)
                    )
                    recent_play_result = await self.session.execute(recent_play_stmt)
                    recent_play = recent_play_result.scalar_one_or_none()
                    if recent_play:
                        plays.append(await self.mapper.to_domain(recent_play))
                
                return plays
                
            elif sort_by == "last_played_desc":
                # Get most recent play per track, ordered by played_at desc
                # Using window function to get the latest play per track
                from sqlalchemy import row_number
                from sqlalchemy.sql import and_
                
                subquery = (
                    select(
                        self.model_class,
                        row_number()
                        .over(
                            partition_by=self.model_class.track_id,
                            order_by=self.model_class.played_at.desc()
                        )
                        .label("rn")
                    )
                    .where(self.model_class.is_deleted == False)  # noqa: E712
                ).subquery()
                
                stmt = (
                    select(subquery)
                    .where(subquery.c.rn == 1)
                    .order_by(subquery.c.played_at.desc())
                    .limit(limit)
                )
                
                result = await self.session.execute(stmt)
                rows = result.fetchall()
                
                # Convert to domain models
                plays = []
                for row in rows:
                    # Extract the track play data from the row
                    play_data = {
                        col.name: getattr(row, col.name)
                        for col in self.model_class.__table__.columns
                    }
                    db_play = self.model_class(**play_data)
                    plays.append(await self.mapper.to_domain(db_play))
                
                return plays
                
            elif sort_by == "title_asc":
                # Join with tracks table for title sorting
                stmt = (
                    select(self.model_class)
                    .join(DBTrack, self.model_class.track_id == DBTrack.id)
                    .where(self.model_class.is_deleted == False)  # noqa: E712
                    .order_by(DBTrack.title)
                    .limit(limit)
                )
                
                result = await self.session.execute(stmt)
                db_models = result.scalars().all()
                return [await self.mapper.to_domain(model) for model in db_models]
                
            elif sort_by == "random":
                stmt = (
                    select(self.model_class)
                    .where(self.model_class.is_deleted == False)  # noqa: E712
                    .order_by(func.random())
                    .limit(limit)
                )
                
                result = await self.session.execute(stmt)
                db_models = result.scalars().all()
                return [await self.mapper.to_domain(model) for model in db_models]
        
        # Use base repository for simple field sorting
        order_by = None
        if sort_by == "played_at_desc":
            order_by = ("played_at", False)  # DESC
        elif sort_by == "first_played_asc":
            order_by = ("played_at", True)   # ASC
        
        return await self.find_by([], limit=limit, order_by=order_by)
