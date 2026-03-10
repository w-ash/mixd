"""Track metrics repository for tracking play counts and other metrics."""

# pyright: reportExplicitAny=false, reportAny=false
# Legitimate Any: metric value types from aggregation queries

from datetime import UTC, datetime, timedelta
from typing import Any, override

from attrs import define
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_logger
from src.infrastructure.persistence.database.db_models import DBTrackMetric
from src.infrastructure.persistence.repositories.base_repo import (
    BaseModelMapper,
    BaseRepository,
)
from src.infrastructure.persistence.repositories.repo_decorator import db_operation

logger = get_logger(__name__)


@define(frozen=True, slots=True)
class TrackMetricMapper(BaseModelMapper[DBTrackMetric, dict[str, Any]]):
    """Mapper for track metrics to simple dictionaries."""

    @override
    @staticmethod
    async def to_domain(db_model: DBTrackMetric) -> dict[str, Any]:
        """Convert DB metric to dictionary representation."""
        return {
            "id": db_model.id,
            "track_id": db_model.track_id,
            "connector_name": db_model.connector_name,
            "metric_type": db_model.metric_type,
            "value": db_model.value,
            "collected_at": db_model.collected_at,
        }

    @override
    @staticmethod
    def to_db(domain_model: dict[str, Any]) -> DBTrackMetric:
        """Convert dictionary to DB model."""
        return DBTrackMetric(
            track_id=domain_model.get("track_id"),
            connector_name=domain_model.get("connector_name"),
            metric_type=domain_model.get("metric_type"),
            value=domain_model.get("value"),
            collected_at=domain_model.get("collected_at", datetime.now(UTC)),
        )

    @override
    @staticmethod
    def get_default_relationships() -> list[str]:
        """Get default relationships to load.

        DBTrackMetric has no relationships that need eager loading.
        """
        return []


class TrackMetricsRepository(BaseRepository[DBTrackMetric, dict[str, Any]]):
    """Repository for track metrics operations."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize repository with session."""
        super().__init__(
            session=session,
            model_class=DBTrackMetric,
            mapper=TrackMetricMapper(),
        )

    # -------------------------------------------------------------------------
    # PUBLIC API METHODS
    # -------------------------------------------------------------------------

    @db_operation("get_track_metrics")
    async def get_track_metrics(
        self,
        track_ids: list[int],
        metric_type: str = "play_count",
        connector: str = "lastfm",
        max_age_hours: float = 24.0,
    ) -> dict[int, float]:
        """Get cached metrics with TTL awareness."""
        if not track_ids:
            return {}

        # Calculate cutoff time
        cutoff = datetime.now(UTC) - timedelta(hours=max_age_hours)

        # Use find_by with optimized conditions
        result = await self.find_by(
            conditions=[
                self.model_class.track_id.in_(track_ids),
                self.model_class.connector_name == connector,
                self.model_class.metric_type == metric_type,
                self.model_class.collected_at >= cutoff,
            ],
            order_by=("collected_at", False),  # DESC order
        )

        # Process results - only keep most recent value per track
        metrics_dict: dict[int, float] = {}
        for metric in result:
            track_id = metric["track_id"]
            if track_id not in metrics_dict:
                metrics_dict[track_id] = metric["value"]

        logger.debug(
            f"Retrieved {len(metrics_dict)}/{len(track_ids)} track metrics",
            metric_type=metric_type,
            connector=connector,
        )

        return metrics_dict

    @db_operation("save_track_metrics")
    async def save_track_metrics(
        self,
        metrics: list[tuple[int, str, str, float]],
    ) -> int:
        """Save metrics for multiple tracks efficiently with SQLite upsert.

        Prevents duplicate metrics by using the unique constraint defined in
        the DBTrackMetric model and SQLite's ON CONFLICT clause to perform
        an update when a constraint violation occurs.
        """
        if not metrics:
            return 0

        now = datetime.now(UTC)

        # Use SQLAlchemy's dialect-specific upsert functionality
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        # Prepare values for insertion
        values: list[dict[str, Any]] = []
        for i, metric_tuple in enumerate(metrics):
            try:
                track_id, connector_name, metric_type, value = metric_tuple
                values.append({
                    "track_id": track_id,
                    "connector_name": connector_name,
                    "metric_type": metric_type,
                    "value": value,
                    "collected_at": now,
                })
            except ValueError as e:
                logger.error(
                    f"Error unpacking metric tuple at index {i}: {metric_tuple} "
                    + f"(length: {len(metric_tuple) if hasattr(metric_tuple, '__len__') else 'unknown'}). "
                    + f"Expected 4 values, got {len(metric_tuple) if hasattr(metric_tuple, '__len__') else 'unknown'}: {e}"
                )
                continue

        # Build the insert statement with ON CONFLICT clause
        # This uses the unique constraint defined in the DBTrackMetric model
        stmt = sqlite_insert(DBTrackMetric).values(values)

        # Add the ON CONFLICT clause to update existing metrics
        stmt = stmt.on_conflict_do_update(
            index_elements=["track_id", "connector_name", "metric_type"],
            set_={
                "value": stmt.excluded.value,
                "collected_at": stmt.excluded.collected_at,
            },
        )

        _ = await self.session.execute(stmt)
        await self.session.flush()

        return len(metrics)
