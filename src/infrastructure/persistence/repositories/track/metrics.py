"""Track metrics repository for tracking play counts and other metrics."""

from datetime import UTC, datetime, timedelta
from typing import override
from uuid import UUID

from attrs import define
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_logger
from src.domain.entities.track import TrackMetric
from src.infrastructure.persistence.database.db_models import DBTrackMetric
from src.infrastructure.persistence.repositories.base_repo import (
    BaseModelMapper,
    BaseRepository,
)
from src.infrastructure.persistence.repositories.repo_decorator import db_operation

logger = get_logger(__name__)


@define(frozen=True, slots=True)
class TrackMetricMapper(BaseModelMapper[DBTrackMetric, TrackMetric]):
    """Bidirectional mapper between ``DBTrackMetric`` and the ``TrackMetric``
    domain entity.
    """

    @override
    @staticmethod
    async def to_domain(db_model: DBTrackMetric) -> TrackMetric:
        """Convert DB metric to domain entity."""
        return TrackMetric(
            id=db_model.id,
            track_id=db_model.track_id,
            connector_name=db_model.connector_name,
            metric_type=db_model.metric_type,
            value=db_model.value,
            collected_at=db_model.collected_at,
        )

    @override
    @staticmethod
    def to_db(domain_model: TrackMetric) -> DBTrackMetric:
        """Convert domain entity to DB model."""
        return DBTrackMetric(
            track_id=domain_model.track_id,
            connector_name=domain_model.connector_name,
            metric_type=domain_model.metric_type,
            value=domain_model.value,
            collected_at=domain_model.collected_at,
        )


class TrackMetricsRepository(BaseRepository[DBTrackMetric, TrackMetric]):
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
        track_ids: list[UUID],
        metric_type: str = "play_count",
        connector: str = "lastfm",
        max_age_hours: float = 24.0,
    ) -> dict[UUID, float]:
        """Get cached metrics with TTL awareness.

        Returns the flattened ``{track_id: value}`` shape that callers expect —
        the underlying ``TrackMetric`` entity carries the full record but the
        application layer only needs the per-track value here.
        """
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
        metrics_dict: dict[UUID, float] = {}
        for metric in result:
            if metric.track_id not in metrics_dict:
                metrics_dict[metric.track_id] = metric.value

        logger.debug(
            f"Retrieved {len(metrics_dict)}/{len(track_ids)} track metrics",
            metric_type=metric_type,
            connector=connector,
        )

        return metrics_dict

    @db_operation("save_track_metrics")
    async def save_track_metrics(self, metrics: list[TrackMetric]) -> int:
        """Save metrics for multiple tracks efficiently with PostgreSQL upsert.

        Takes ``TrackMetric`` entities (symmetric with ``to_domain``) and
        bulk-upserts via ``pg_insert(...).on_conflict_do_update``. The unique
        constraint on ``(track_id, connector_name, metric_type)`` collapses
        repeated samples to the most recent value.
        """
        if not metrics:
            return 0

        # Build per-row insert dicts directly from entity fields. dict[str, object]
        # at the boundary keeps SQLAlchemy happy without leaking Any.
        values: list[dict[str, object]] = [
            {
                "track_id": m.track_id,
                "connector_name": m.connector_name,
                "metric_type": m.metric_type,
                "value": m.value,
                "collected_at": m.collected_at,
            }
            for m in metrics
        ]

        # PostgreSQL upsert via ON CONFLICT
        stmt = pg_insert(DBTrackMetric).values(values)

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
