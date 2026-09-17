"""Audit rows for long-running SSE operations."""

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

from src.domain.entities.shared import JsonDict
from src.infrastructure.persistence.database.models.base import (
    BaseEntity,
    PgJsonb,
    PgUuidCol,
    UuidType,
)


class DBOperationRun(BaseEntity):
    """Audit row for a long-running SSE operation.

    Written at kickoff with ``status="running"`` by the seam-level
    ``OperationRunRecorder``; updated on terminal events with the final
    status, merged ``counts``, and accumulated ``issues``. ``counts`` and
    ``issues`` are JSONB because each operation type defines its own
    payload shape.
    """

    __tablename__: str = "operation_runs"
    __table_args__: tuple[SchemaItem, ...] = (
        Index("ix_operation_runs_user_id_started_at", "user_id", "started_at"),
        # Migration 048: the startup reaper and the busy probe scan running rows
        # bounded by started_at; the partial index stays tiny as history grows.
        Index(
            "ix_operation_runs_running_started_at",
            "started_at",
            postgresql_where=text("status = 'running'"),
        ),
    )

    user_id: Mapped[str] = mapped_column(String(), nullable=False)
    operation_type: Mapped[str] = mapped_column(String(64), nullable=False)
    # SSE registry's queue key — lets /operations/{id}/run-snapshot and
    # /operations/active resolve operation_id -> audit row (and hand the
    # frontend an operation_id to re-attach the live stream). Migration 031.
    operation_id: Mapped[str | None] = mapped_column(
        String(36), unique=True, index=True, nullable=True
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    counts: Mapped[JsonDict] = mapped_column(
        PgJsonb, nullable=False, server_default=text("'{}'::jsonb"), default=dict
    )
    issues: Mapped[list[JsonDict]] = mapped_column(
        PgJsonb, nullable=False, server_default=text("'[]'::jsonb"), default=list
    )
    # Parameters needed to re-invoke this operation — connector_name +
    # sync_direction for an import — so "Retry failed only" can reconstruct the
    # call server-side from the row alone (failed item ids come from issues).
    # Connector config only (strings), never ids or user_id. Migration 032.
    request_params: Mapped[JsonDict] = mapped_column(
        PgJsonb, nullable=False, server_default=text("'{}'::jsonb"), default=dict
    )
    # Provenance: the schedule that fired this sync, if any. ON DELETE SET NULL
    # so deleting a schedule preserves its historical runs (migration 026).
    triggered_by_schedule_id: Mapped[UuidType | None] = mapped_column(
        PgUuidCol(as_uuid=True),
        ForeignKey("schedules.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Attribution: who initiated this run — "manual" (default), "assistant"
    # (an AI-agent-launched background op), or "schedule". Rendered as a badge
    # in the run log so agent activity is visible alongside human-initiated
    # runs. Migration 037.
    initiated_by: Mapped[str] = mapped_column(
        String(16), nullable=False, default="manual", server_default="manual"
    )
    # Finer-grained provenance beside initiated_by: which surface asked for this
    # run — "web", "mcp", or "workflow:<run_id>". Lets the run log distinguish a
    # poll fired by a page view from one a scheduled workflow demanded.
    # Migration 043.
    trigger_detail: Mapped[str | None] = mapped_column(String(64), nullable=True)
