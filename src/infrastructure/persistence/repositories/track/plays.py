"""Track repository for play operations."""

from datetime import datetime
from typing import Any, override

from attrs import define
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from toolz import groupby

from src.config import get_logger
from src.domain.entities import TrackPlay, ensure_utc
from src.infrastructure.persistence.database.db_models import DBTrackPlay
from src.infrastructure.persistence.repositories.base_repo import (
    BaseModelMapper,
    BaseRepository,
)
from src.infrastructure.persistence.repositories.repo_decorator import db_operation

logger = get_logger(__name__)


def _chunked[T](items: list[T], size: int) -> list[list[T]]:
    """Split items into fixed-size chunks. Typed alternative to toolz.partition_all."""
    return [items[i : i + size] for i in range(0, len(items), size)]


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
        inserted_count = len(play_data) if isinstance(result, list) else result
        return (inserted_count, duplicate_count)

    async def _find_existing_plays(self, plays: list[TrackPlay]) -> set[tuple]:
        """Find existing plays that match the lookup keys.

        Uses batched queries to avoid SQLite expression tree limit (max depth 1000).
        Processes plays in chunks of 200 to stay well under the limit.
        """
        if not plays:
            return set()

        existing_keys = set()

        # Batch plays to avoid SQLite expression tree limit (max depth 1000)
        # Using partition_all from toolz following CLAUDE.md functional patterns
        batch_size = (
            200  # Well under SQLite's 1000 limit, allows for other query complexity
        )

        for batch in _chunked(plays, batch_size):
            # Build conditions for this batch
            conditions = []
            for play in batch:
                condition = (
                    (self.model_class.track_id == play.track_id)
                    & (self.model_class.service == play.service)
                    & (self.model_class.played_at == play.played_at)
                    & (self.model_class.ms_played == play.ms_played)
                )
                conditions.append(condition)

            # Combine batch conditions with OR
            if len(conditions) == 1:
                combined_condition = conditions[0]
            else:
                combined_condition = conditions[0]
                for condition in conditions[1:]:
                    combined_condition |= condition

            # Query for existing plays in this batch
            existing_db_plays = await self.find_by([combined_condition])

            # Convert to set of tuples for fast lookup
            # Normalize timezone to handle timezone-aware vs timezone-naive comparison
            for play in existing_db_plays:
                # Ensure played_at has UTC timezone for consistent comparison using utility function
                played_at = ensure_utc(play.played_at)
                existing_keys.add((
                    play.track_id,
                    play.service,
                    played_at,
                    play.ms_played,
                ))

        return existing_keys

    def _filter_duplicates(
        self, plays: list[TrackPlay], existing_keys: set[tuple]
    ) -> list[TrackPlay]:
        """Filter out plays that already exist in the database."""
        new_plays = []

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
            # Ensure timezone consistency using utility function
            start_aware = ensure_utc(period_start)
            end_aware = ensure_utc(period_end)

            # Both timestamps are required for period comparison
            if start_aware is None or end_aware is None:
                raise ValueError("Period start and end must be non-None timestamps")

            logger.debug(
                "Period play comparison setup",
                start_aware=start_aware,
                start_aware_tz=start_aware.tzinfo,
                end_aware=end_aware,
                end_aware_tz=end_aware.tzinfo,
                original_start_tz=period_start.tzinfo,
                original_end_tz=period_end.tzinfo,
            )

            def count_period_plays(track_plays):
                matching_plays = []
                for play in track_plays:
                    try:
                        # Defensive validation - ensure play.played_at is timezone-aware using utility function
                        play_played_at_aware = ensure_utc(play.played_at)
                        if play_played_at_aware is None:
                            logger.warning(
                                "Skipping play with None played_at",
                                play_id=play.id,
                                track_id=play.track_id,
                            )
                            continue

                        if play.played_at.tzinfo is None:
                            logger.warning(
                                "Found timezone-naive play.played_at, converted to UTC",
                                play_id=play.id,
                                track_id=play.track_id,
                                played_at=play.played_at,
                            )

                        if start_aware <= play_played_at_aware <= end_aware:
                            matching_plays.append(play)

                    except TypeError as e:
                        logger.error(
                            "Datetime comparison failed despite defensive measures",
                            start_aware=start_aware,
                            start_tz=getattr(start_aware, "tzinfo", None),
                            play_played_at=play.played_at,
                            play_tz=getattr(play.played_at, "tzinfo", None),
                            end_aware=end_aware,
                            end_tz=getattr(end_aware, "tzinfo", None),
                            error=str(e),
                            play_id=play.id,
                        )
                        # Skip this play rather than failing the entire operation
                        continue

                return len(matching_plays)

            result["period_plays"] = {
                track_id: count_period_plays(track_plays)
                for track_id, track_plays in plays_by_track.items()
            }

        # Ensure all requested track_ids are present in results
        for metric_name, metric_data in result.items():
            for track_id in track_ids:
                if track_id not in metric_data:
                    if metric_name in {"total_plays", "period_plays"}:
                        metric_data[track_id] = 0
                    else:  # last_played_dates
                        metric_data[track_id] = None

        return result

    @db_operation("get_recent_plays")
    async def get_recent_plays(
        self, limit: int = 100, sort_by: str | None = None
    ) -> list[TrackPlay]:
        """Get recent plays with optional sorting."""
        # Handle special sorting cases that require custom queries or aggregations
        if sort_by in ["total_plays_desc", "last_played_desc", "title_asc", "random"]:
            from sqlalchemy import func, select

            from src.infrastructure.persistence.database.db_models import DBTrack

            if sort_by == "total_plays_desc":
                # Get plays grouped by track_id, ordered by count
                stmt = (
                    select(self.model_class.track_id, func.count().label("play_count"))
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
                from sqlalchemy.sql import func

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
                    .order_by(DBTrack.title)
                    .limit(limit)
                )

                result = await self.session.execute(stmt)
                db_models = result.scalars().all()
                return [await self.mapper.to_domain(model) for model in db_models]

            elif sort_by == "random":
                stmt = select(self.model_class).order_by(func.random()).limit(limit)

                result = await self.session.execute(stmt)
                db_models = result.scalars().all()
                return [await self.mapper.to_domain(model) for model in db_models]

        # Use base repository for simple field sorting
        order_by = None
        if sort_by == "played_at_desc":
            order_by = ("played_at", False)  # DESC
        elif sort_by == "first_played_asc":
            order_by = ("played_at", True)  # ASC

        return await self.find_by([], limit=limit, order_by=order_by)
