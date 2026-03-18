"""Track repository for play operations."""

# pyright: reportExplicitAny=false, reportAny=false
# Legitimate Any: SQLAlchemy column types, JSON fields

from collections.abc import Sequence
from datetime import datetime
from typing import Any, Literal, override

from attrs import define
from sqlalchemy import func, select, tuple_
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

# (track_id, service, played_at, ms_played) — deduplication key for play records
type PlayLookupKey = tuple[int | None, str, datetime | None, int | None]


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

    @db_operation("count_all_plays")
    async def count_all_plays(self) -> int:
        """Count all play records in the database."""
        stmt = self.count()
        result = await self.session.execute(stmt)
        return result.scalar_one()

    @db_operation("count_plays_by_service")
    async def count_plays_by_service(self) -> dict[str, int]:
        """Count play records grouped by service."""
        stmt = select(
            DBTrackPlay.service,
            func.count(DBTrackPlay.id),
        ).group_by(DBTrackPlay.service)
        result = await self.session.execute(stmt)
        return {service: count for service, count in result.tuples().all()}

    @db_operation("bulk_insert_plays")
    async def bulk_insert_plays(self, plays: list[TrackPlay]) -> tuple[int, int]:
        """Bulk insert track plays efficiently with deduplication.

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

        # Deduplicate against existing plays in database
        existing_plays = await self._find_existing_plays(valid_plays)
        new_plays = self._filter_duplicates(valid_plays, existing_plays)

        duplicate_count = len(valid_plays) - len(new_plays)
        if duplicate_count > 0:
            logger.info(
                f"Filtered out {duplicate_count} duplicate plays (inserting {len(new_plays)} new plays)"
            )

        if not new_plays:
            logger.info("All plays were duplicates - no new plays to insert")
            return (0, duplicate_count)

        play_data = [
            {
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
            for play in new_plays
        ]

        result = await self.bulk_upsert(
            play_data,
            lookup_keys=["track_id", "service", "played_at", "ms_played"],
            return_models=False,
        )

        # Return count of actually inserted records and duplicate count
        return (result, duplicate_count)

    async def _find_existing_plays(self, plays: list[TrackPlay]) -> set[PlayLookupKey]:
        """Find existing plays matching deduplication keys.

        Uses PostgreSQL tuple IN for efficient multi-column lookup,
        batched to avoid oversized query strings on large imports.
        """
        if not plays:
            return set()

        from src.config.constants import BusinessLimits

        keys = [(p.track_id, p.service, p.played_at, p.ms_played) for p in plays]
        batch_size = BusinessLimits.TUPLE_IN_BATCH_SIZE
        existing: set[PlayLookupKey] = set()

        for i in range(0, len(keys), batch_size):
            batch = keys[i : i + batch_size]
            stmt = select(
                DBTrackPlay.track_id,
                DBTrackPlay.service,
                DBTrackPlay.played_at,
                DBTrackPlay.ms_played,
            ).where(
                tuple_(
                    DBTrackPlay.track_id,
                    DBTrackPlay.service,
                    DBTrackPlay.played_at,
                    DBTrackPlay.ms_played,
                ).in_(batch)
            )
            result = await self.session.execute(stmt)
            existing.update(
                (row.track_id, row.service, ensure_utc(row.played_at), row.ms_played)
                for row in result.all()
            )

        return existing

    def _filter_duplicates(
        self, plays: list[TrackPlay], existing_keys: set[PlayLookupKey]
    ) -> list[TrackPlay]:
        """Filter out plays that already exist in the database."""
        new_plays: list[TrackPlay] = []

        for play in plays:
            # Normalize timezone for consistent comparison using utility function
            played_at = ensure_utc(play.played_at)

            play_key = (play.track_id, play.service, played_at, play.ms_played)
            if play_key not in existing_keys:
                new_plays.append(play)

        return new_plays

    @db_operation("get_plays_by_batch")
    async def get_plays_by_batch(self, import_batch_id: str) -> list[TrackPlay]:
        """Get all plays from a specific import batch."""
        return await self.find_by([
            self.model_class.import_batch_id == import_batch_id,
        ])

    @db_operation("get_play_aggregations")
    async def get_play_aggregations(
        self,
        track_ids: list[int],
        metrics: list[str],
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
                .where(DBTrackPlay.track_id.in_(track_ids))
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
        self, limit: int = 100, sort_by: PlaySortBy | None = None
    ) -> list[TrackPlay]:
        """Get recent plays with optional sorting."""
        from src.infrastructure.persistence.database.db_models import DBTrack

        # Handle special sorting cases that require custom queries or aggregations
        if sort_by in ["total_plays_desc", "last_played_desc", "title_asc", "random"]:
            if sort_by == "total_plays_desc":
                # CTE: top tracks by play count
                top_tracks = (
                    select(
                        self.model_class.track_id,
                        func.count().label("cnt"),
                    )
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
                    ).join(
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
                    )
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
                    .join(DBTrack, self.model_class.track_id == DBTrack.id)
                    .order_by(DBTrack.title)
                    .limit(limit)
                )

                query_result = await self.session.execute(stmt)
                db_models = query_result.scalars().all()
                return [await self.mapper.to_domain(model) for model in db_models]

            elif sort_by == "random":
                stmt = select(self.model_class).order_by(func.random()).limit(limit)

                query_result = await self.session.execute(stmt)
                db_models = query_result.scalars().all()
                return [await self.mapper.to_domain(model) for model in db_models]

        # Use base repository for simple field sorting
        order_by = None
        if sort_by == "played_at_desc":
            order_by = ("played_at", False)  # DESC
        elif sort_by == "first_played_asc":
            order_by = ("played_at", True)  # ASC

        return await self.find_by([], limit=limit, order_by=order_by)

    @db_operation("find_plays_in_time_range")
    async def find_plays_in_time_range(
        self,
        track_ids: list[int],
        start: datetime,
        end: datetime,
    ) -> list[TrackPlay]:
        """Find existing plays for given tracks within a time range.

        Used by cross-source deduplication to find candidate matches before
        running the dedup algorithm. Leverages ``ix_track_plays_track_played``
        composite index for efficient lookups.

        Args:
            track_ids: Canonical track IDs to search for.
            start: Range start (inclusive, timezone-aware UTC).
            end: Range end (inclusive, timezone-aware UTC).

        Returns:
            Matching plays within the time range.
        """
        if not track_ids:
            return []

        return await self.find_by([
            self.model_class.track_id.in_(track_ids),
            self.model_class.played_at >= start,
            self.model_class.played_at <= end,
        ])

    @db_operation("bulk_update_play_source_services")
    async def bulk_update_play_source_services(
        self,
        updates: list[tuple[int, dict[str, Any]]],
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
