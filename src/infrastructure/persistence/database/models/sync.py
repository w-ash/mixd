"""Incremental-sync checkpoints and adaptive-polling state."""

from datetime import datetime

from sqlalchemy import (
    DateTime,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.schema import SchemaItem

from src.domain.entities.shared import JsonDict
from src.infrastructure.persistence.database.models.base import (
    BaseEntity,
    PgJsonb,
)


class DBSyncCheckpoint(BaseEntity):
    """Sync state tracking for incremental operations.

    Represents synchronization state for external services.
    """

    __tablename__: str = "sync_checkpoints"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint(
            "user_id", "service", "entity_type", name="uq_sync_checkpoints_user_id"
        ),
    )

    user_id: Mapped[str] = mapped_column(String(), nullable=False)
    service: Mapped[str] = mapped_column(String(32))  # 'spotify', 'lastfm'
    entity_type: Mapped[str] = mapped_column(String(32))  # 'likes', 'plays'
    last_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cursor: Mapped[str | None] = mapped_column(String(1024))  # continuation token
    remote_total: Mapped[int | None] = mapped_column(
        nullable=True
    )  # total items reported by remote service
    # Adaptive-polling state (migration 043). Deliberately separate from
    # last_timestamp: that records when the user last *listened*, this records
    # when we last *checked* — an idle account's last_timestamp ages forever.
    last_polled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Backoff counter + near-overflow markers. Schemaless so policy tuning needs
    # no migration; read through PollState.from_json, which tolerates absences.
    poll_state: Mapped[JsonDict] = mapped_column(
        PgJsonb, nullable=False, server_default=text("'{}'::jsonb"), default=dict
    )
    # Single-flight lease, not a lock: a claim stamp reclaimed after a TTL, in
    # the same shape as schedules.started_at. Repo-internal — deliberately not on
    # the SyncCheckpoint entity, so no caller can round-trip it by accident.
    poll_claimed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
