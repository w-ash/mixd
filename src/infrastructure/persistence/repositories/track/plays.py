"""Track repository for play operations."""

# pyright: reportExplicitAny=false, reportAny=false
# Legitimate Any: SQLAlchemy column types, JSON fields

from collections.abc import Sequence
from datetime import datetime
from typing import Any, Literal, override
from uuid import UUID

from attrs import define
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_logger
from src.domain.entities import TrackPlay, ensure_utc
from src.domain.repositories.interfaces import PlayAggregationResult
from src.infrastructure.persistence.database.db_models import DBTrackPlay
from src.infrastructure.persistence.repositories.base_repo import (
    BaseModelMapper,
    BaseRepository,
)
from src.infrastructure.persistence.repositories.repo_decorator import db_operation

type PlaySortBy = Literal[
    "total_plays_desc",
    "last_played_desc",
    "title_asc",
    "random",
    "played_at_desc",
    "first_played_asc",
]

logger = get_logger(__name__)


@define(frozen=True, slots=True)
class TrackPlayMapper(BaseModelMapper[DBTrackPlay, TrackPlay]):
    """Maps between DBTrackPlay and TrackPlay domain models."""

    @override
    @staticmethod
    def get_default_relationships() -> list[str]:
        """Return relationships to eagerly load for track plays."""
        return []  # Don't eagerly load track by default for performance

    @override
    @staticmethod
    async def to_domain(db_model: DBTrackPlay) -> TrackPlay:
        """Convert database play to domain model."""
        # Ensure datetime fields are timezone-aware using utility function
        # played_at is required (non-None) in the database model
        played_at = ensure_utc(db_model.played_at)
        if played_at is None:
            raise ValueError("TrackPlay requires a non-None played_at timestamp")
        import_timestamp = ensure_utc(db_model.import_timestamp)

        return TrackPlay(
            track_id=db_model.track_id,
            service=db_model.service,
            played_at=played_at,
            user_id=db_model.user_id,
            ms_played=db_model.ms_played,
            context=db_model.context,
            id=db_model.id,
            source_services=db_model.source_services,
            import_timestamp=import_timestamp,
            import_source=db_model.import_source,
            import_batch_id=db_model.import_batch_id,
        )

    @override
    @staticmethod
    def to_db(domain_model: TrackPlay) -> DBTrackPlay:
        """Convert domain play to database model."""
        return DBTrackPlay(
            user_id=domain_model.user_id,
            track_id=domain_model.track_id,
            service=domain_model.service,
            played_at=domain_model.played_at,
            ms_played=domain_model.ms_played,
            context=domain_model.context,
            source_services=domain_model.source_services,
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
    async def bulk_insert_plays(self, plays: list[TrackPlay]) -> tuple[int, int]:
        """Bulk insert track plays with ON CONFLICT DO NOTHING deduplication.

        PostgreSQL's unique constraint ``uq_track_plays_deduplication``
        (track_id, service, played_at, ms_played) atomically skips duplicates.
        No pre-query needed.

        Returns:
            tuple[int, int]: (inserted_count, duplicate_count)
        """
        if not plays:
            return (0, 0)

        # Filter out plays with NULL track_id to prevent constraint violations
        valid_plays = [play for play in plays if play.track_id is not None]

        if not valid_plays:
            logger.warning(
                f"Filtered out all {len(plays)} plays due to NULL track_id - no plays to insert"
            )
            return (0, 0)

        if len(valid_plays) < len(plays):
            logger.warning(
                f"Filtered out {len(plays) - len(valid_plays)} plays with NULL track_id (kept {len(valid_plays)} valid plays)"
            )

        play_data = [
            {
                "user_id": play.user_id,
                "track_id": play.track_id,
                "service": play.service,
                "played_at": play.played_at,
                "ms_played": play.ms_played,
                "context": play.context,
                "source_services": play.source_services,
                "import_timestamp": play.import_timestamp,
                "import_source": play.import_source,
                "import_batch_id": play.import_batch_id,
            }
            for play in valid_plays
        ]

        conflict_keys = ["user_id", "track_id", "service", "played_at", "ms_played"]
        inserted = await self.bulk_insert_ignore_conflicts(play_data, conflict_keys)

        duplicate_count = len(valid_plays) - inserted
        if duplicate_count > 0:
            logger.info(
                f"Skipped {duplicate_count} duplicate plays (inserted {inserted} new plays)"
            )

        return (inserted, duplicate_count)

    @db_operation("get_plays_by_batch")
    async def get_plays_by_batch(self, import_batch_id: str) -> list[TrackPlay]:
        """Get all plays from a specific import batch."""
        return await self.find_by([
            self.model_class.import_batch_id == import_batch_id,
        ])

    @db_operation("get_play_aggregations")
    async def get_play_aggregations(
        self,
        track_ids: list[UUID],
        metrics: list[str],
        *,
        user_id: str,
        period_start: datetime | None = None,
        period_end: datetime | None = None,
    ) -> PlayAggregationResult:
        """Get aggregated play data via database-side GROUP BY.

        Uses PostgreSQL aggregation functions instead of loading all plays into
        Python. For 100k plays across 1k tracks, this returns ~1k rows instead
        of 100k ORM objects.

        Args:
            track_ids: List of track IDs to get play data for
            metrics: List of metrics to calculate ["total_plays", "last_played_dates", "first_played_dates", "period_plays"]
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

        result: PlayAggregationResult = {}

        # Build a single aggregation query for base metrics (total, first, last)
        base_metrics = {"total_plays", "last_played_dates", "first_played_dates"}
        requested_base = base_metrics & set(metrics)

        if requested_base:
            columns: list[Any] = [DBTrackPlay.track_id]
            if "total_plays" in requested_base:
                columns.append(func.count().label("total"))
            if "last_played_dates" in requested_base:
                columns.append(func.max(DBTrackPlay.played_at).label("last_played"))
            if "first_played_dates" in requested_base:
                columns.append(func.min(DBTrackPlay.played_at).label("first_played"))

            stmt = (
                select(*columns)
                .where(
                    DBTrackPlay.track_id.in_(track_ids),
                    DBTrackPlay.user_id == user_id,
                )
                .group_by(DBTrackPlay.track_id)
            )
            rows = (await self.session.execute(stmt)).all()

            if "total_plays" in requested_base:
                result["total_plays"] = {row.track_id: row.total for row in rows}
            if "last_played_dates" in requested_base:
                result["last_played_dates"] = {
                    row.track_id: row.last_played for row in rows
                }
            if "first_played_dates" in requested_base:
                result["first_played_dates"] = {
                    row.track_id: row.first_played for row in rows
                }

        # Period plays: separate query with date range WHERE clause
        if "period_plays" in metrics and period_start and period_end:
            start_aware = ensure_utc(period_start)
            end_aware = ensure_utc(period_end)

            if start_aware is None or end_aware is None:
                raise ValueError("Period start and end must be non-None timestamps")

            period_stmt = (
                select(DBTrackPlay.track_id, func.count().label("total"))
                .where(
                    DBTrackPlay.track_id.in_(track_ids),
                    DBTrackPlay.user_id == user_id,
                    DBTrackPlay.played_at >= start_aware,
                    DBTrackPlay.played_at <= end_aware,
                )
                .group_by(DBTrackPlay.track_id)
            )
            period_rows = (await self.session.execute(period_stmt)).all()
            result["period_plays"] = {row.track_id: row.total for row in period_rows}

        # Backfill missing track_ids with defaults for each requested metric
        if "total_plays" in result:
            for track_id in track_ids:
                if track_id not in result["total_plays"]:
                    result["total_plays"][track_id] = 0
        if "period_plays" in result:
            for track_id in track_ids:
                if track_id not in result["period_plays"]:
                    result["period_plays"][track_id] = 0
        if "first_played_dates" in result:
            for track_id in track_ids:
                if track_id not in result["first_played_dates"]:
                    result["first_played_dates"][track_id] = None
        if "last_played_dates" in result:
            for track_id in track_ids:
                if track_id not in result["last_played_dates"]:
                    result["last_played_dates"][track_id] = None

        return result

    async def _rows_to_domain(self, rows: Sequence[Any]) -> list[TrackPlay]:
        """Convert raw subquery rows to domain models via column reflection."""
        plays: list[TrackPlay] = []
        for row in rows:
            play_data = {
                col.name: getattr(row, col.name)
                for col in self.model_class.__table__.columns
            }
            db_play = self.model_class(**play_data)
            plays.append(await self.mapper.to_domain(db_play))
        return plays

    @db_operation("get_recent_plays")
    async def get_recent_plays(
        self, *, user_id: str, limit: int = 100, sort_by: PlaySortBy | None = None
    ) -> list[TrackPlay]:
        """Get recent plays with optional sorting, scoped to user."""
        from src.infrastructure.persistence.database.db_models import DBTrack

        user_filter = self.model_class.user_id == user_id

        # Handle special sorting cases that require custom queries or aggregations
        if sort_by in ["total_plays_desc", "last_played_desc", "title_asc", "random"]:
            if sort_by == "total_plays_desc":
                # CTE: top tracks by play count
                top_tracks = (
                    select(
                        self.model_class.track_id,
                        func.count().label("cnt"),
                    )
                    .where(user_filter)
                    .group_by(self.model_class.track_id)
                    .order_by(func.count().desc())
                    .limit(limit)
                ).cte("top_tracks")

                # Get latest play per top track using row_number window function
                ranked = (
                    select(
                        self.model_class,
                        top_tracks.c.cnt,
                        func
                        .row_number()
                        .over(
                            partition_by=self.model_class.track_id,
                            order_by=self.model_class.played_at.desc(),
                        )
                        .label("rn"),
                    )
                    .where(user_filter)
                    .join(
                        top_tracks, self.model_class.track_id == top_tracks.c.track_id
                    )
                ).subquery()

                stmt = (
                    select(ranked).where(ranked.c.rn == 1).order_by(ranked.c.cnt.desc())
                )

                query_result = await self.session.execute(stmt)
                return await self._rows_to_domain(query_result.fetchall())

            elif sort_by == "last_played_desc":
                # Get most recent play per track, ordered by played_at desc
                subquery = (
                    select(
                        self.model_class,
                        func
                        .row_number()
                        .over(
                            partition_by=self.model_class.track_id,
                            order_by=self.model_class.played_at.desc(),
                        )
                        .label("rn"),
                    ).where(user_filter)
                ).subquery()

                stmt = (
                    select(subquery)
                    .where(subquery.c.rn == 1)
                    .order_by(subquery.c.played_at.desc())
                    .limit(limit)
                )

                query_result = await self.session.execute(stmt)
                return await self._rows_to_domain(query_result.fetchall())

            elif sort_by == "title_asc":
                # Join with tracks table for title sorting
                stmt = (
                    select(self.model_class)
                    .where(user_filter)
                    .join(DBTrack, self.model_class.track_id == DBTrack.id)
                    .order_by(DBTrack.title)
                    .limit(limit)
                )

                query_result = await self.session.execute(stmt)
                db_models = query_result.scalars().all()
                return [await self.mapper.to_domain(model) for model in db_models]

            elif sort_by == "random":
                stmt = (
                    select(self.model_class)
                    .where(user_filter)
                    .order_by(func.random())
                    .limit(limit)
                )

                query_result = await self.session.execute(stmt)
                db_models = query_result.scalars().all()
                return [await self.mapper.to_domain(model) for model in db_models]

        # Use base repository for simple field sorting
        order_by = None
        if sort_by == "played_at_desc":
            order_by = ("played_at", False)  # DESC
        elif sort_by == "first_played_asc":
            order_by = ("played_at", True)  # ASC

        return await self.find_by([user_filter], limit=limit, order_by=order_by)

    @db_operation("find_plays_in_time_range")
    async def find_plays_in_time_range(
        self,
        track_ids: list[UUID],
        start: datetime,
        end: datetime,
        *,
        user_id: str,
    ) -> list[TrackPlay]:
        """Find existing plays for given tracks within a time range, scoped to user.

        Used by cross-source deduplication to find candidate matches before
        running the dedup algorithm. Leverages ``ix_track_plays_track_played``
        composite index for efficient lookups.

        Args:
            track_ids: Canonical track IDs to search for.
            start: Range start (inclusive, timezone-aware UTC).
            end: Range end (inclusive, timezone-aware UTC).
            user_id: Owner's user ID.

        Returns:
            Matching plays within the time range.
        """
        if not track_ids:
            return []

        return await self.find_by([
            self.model_class.track_id.in_(track_ids),
            self.model_class.played_at >= start,
            self.model_class.played_at <= end,
            self.model_class.user_id == user_id,
        ])

    @db_operation("bulk_update_play_source_services")
    async def bulk_update_play_source_services(
        self,
        updates: list[tuple[UUID, dict[str, Any]]],
    ) -> None:
        """Batch-update cross-source dedup metadata for multiple plays.

        Loads all target plays in a single SELECT, applies per-play updates
        in-memory, and lets SQLAlchemy flush the batch. Replaces the old
        per-play ``update_play_source_services`` to eliminate N+1 queries.

        Args:
            updates: List of (play_id, update_fields) tuples. Each
                update_fields dict may contain ``source_services``,
                ``context``, and/or ``ms_played``.
        """
        if not updates:
            return

        play_ids = [pid for pid, _ in updates]
        stmt = select(self.model_class).where(self.model_class.id.in_(play_ids))
        result = await self.session.execute(stmt)
        plays_by_id = {db_play.id: db_play for db_play in result.scalars().all()}

        for play_id, fields in updates:
            db_play = plays_by_id.get(play_id)
            if db_play is None:
                logger.warning(f"Play {play_id} not found for source_services update")
                continue

            if "source_services" in fields:
                db_play.source_services = fields["source_services"]
            if fields.get("context") is not None:
                db_play.context = fields["context"]
            if fields.get("ms_played") is not None and db_play.ms_played is None:
                db_play.ms_played = fields["ms_played"]
