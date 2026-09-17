"""Schedules that fire workflow runs or background syncs on a cadence."""

from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.schema import SchemaItem

from src.infrastructure.persistence.database.models.base import (
    BaseEntity,
    PgUuidCol,
    UuidType,
)


class DBSchedule(BaseEntity):
    """A schedule that fires a workflow run or a background sync on a cadence.

    Exactly one target is set: ``workflow_id`` (FK, CASCADE) XOR ``sync_target``
    (a free-text ``"service:entity"`` key validated in the application layer).
    No RLS policy — like ``workflow_runs``, per-user isolation is enforced by the
    repository's ``WHERE user_id`` filter, while the scheduler's cross-tenant poll
    (``find_due_schedules``) reads every user's due rows. The CHECK constraints
    (the exclusive target arc, the cadence ranges, and the non-negative counters)
    live in migration 025 per the codebase convention (CHECKs never in
    ``__table_args__``). ``target_type`` is NOT stored — it is derived on the
    ``Schedule`` entity from whether ``workflow_id`` is set.
    """

    __tablename__: str = "schedules"
    __table_args__: tuple[SchemaItem, ...] = (
        # CRUD + list_for_user scan by owner.
        Index("ix_schedules_user_id", "user_id"),
        # Hot path: the poll query is WHERE status='enabled' AND next_run_at<=now.
        Index("ix_schedules_status_next_run_at", "status", "next_run_at"),
        # Reaper hot path: WHERE started_at IS NOT NULL AND started_at<threshold.
        # Partial — only in-flight claims (a small set) are indexed.
        Index(
            "ix_schedules_started_at",
            "started_at",
            postgresql_where=text("started_at IS NOT NULL"),
        ),
        # One workflow-schedule per (user, workflow), one sync-schedule per
        # (user, sync_target). Partial because the unused arm is NULL for the
        # other target type (mirrors uq_workflow_runs_active's partial idiom).
        Index(
            "uq_schedules_workflow_target",
            "user_id",
            "workflow_id",
            unique=True,
            postgresql_where=text("workflow_id IS NOT NULL"),
        ),
        Index(
            "uq_schedules_sync_target",
            "user_id",
            "sync_target",
            unique=True,
            postgresql_where=text("sync_target IS NOT NULL"),
        ),
    )

    user_id: Mapped[str] = mapped_column(String(), nullable=False)
    # Exclusive arc — exactly one is set (enforced by CHECK in migration 025).
    workflow_id: Mapped[UuidType | None] = mapped_column(
        PgUuidCol(as_uuid=True),
        ForeignKey("workflows.id", ondelete="CASCADE"),
        nullable=True,
    )
    sync_target: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Cadence (minute granularity, local to `timezone`): day_of_week NULL ⇒ daily
    # at hour:minute; set (0=Sun…6=Sat) ⇒ weekly on that day. The
    # 'daily'/'weekly'/'interval' kind is derived, never stored (range CHECKs
    # live in migrations 025 and 043).
    hour: Mapped[int] = mapped_column(nullable=False)
    minute: Mapped[int] = mapped_column(nullable=False)
    day_of_week: Mapped[int | None] = mapped_column(nullable=True)
    # Third cadence arm (migration 043): fire every N minutes, ignoring
    # hour/minute. Exclusive with day_of_week. The adaptive play poller rewrites
    # this after every poll with its own backed-off interval, so the scheduler's
    # sleep-until-due loop stays asleep for the stretched duration rather than
    # waking on a fixed tick only to decide it has nothing to do.
    interval_minutes: Mapped[int | None] = mapped_column(nullable=True)
    timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, default="UTC", server_default="UTC"
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="enabled", server_default="enabled"
    )
    # Pre-computed UTC fire time (whole seconds) — the poll index column.
    next_run_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    # Reaper claim marker: non-null while a dispatch is in flight.
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Last-run observability.
    last_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_run_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    # Provenance pointer to the most recent run (workflow_runs.id for a workflow
    # schedule, operation_runs.id for a sync) — no hard FK to avoid a polymorphic
    # constraint; the run tables carry the reverse triggered_by_schedule_id FK.
    last_run_id: Mapped[UuidType | None] = mapped_column(
        PgUuidCol(as_uuid=True), nullable=True
    )
    run_count: Mapped[int] = mapped_column(
        nullable=False, default=0, server_default="0"
    )
    consecutive_failures: Mapped[int] = mapped_column(
        nullable=False, default=0, server_default="0"
    )
