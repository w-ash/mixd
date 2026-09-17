"""Persisted workflow definitions, their version history, and run records."""

from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql.schema import SchemaItem

from src.domain.entities.shared import JsonDict
from src.infrastructure.persistence.database.models.base import (
    BaseEntity,
    DatabaseModel,
    PgJsonb,
    PgUuidCol,
    TimestampMixin,
    UuidType,
)


class DBWorkflow(BaseEntity):
    """Persisted, user-owned workflow definition.

    Stores the complete WorkflowDef as a JSON column alongside identity.
    Every row is a user-owned, editable workflow; built-in templates are a
    file-backed gallery (``list_workflow_defs``), not rows in this table.
    """

    __tablename__: str = "workflows"

    user_id: Mapped[str | None] = mapped_column(String(), nullable=True)
    name: Mapped[str] = mapped_column(String(), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1000))
    definition: Mapped[JsonDict] = mapped_column(PgJsonb, nullable=False)
    definition_version: Mapped[int] = mapped_column(default=1)

    __table_args__: tuple[SchemaItem, ...] = (Index("ix_workflows_user_id", "user_id"),)


class DBWorkflowVersion(DatabaseModel, TimestampMixin):
    """Snapshot of a workflow definition at a point in time.

    Created automatically when UpdateWorkflowUseCase modifies a workflow's
    task pipeline. Stores the *previous* definition before the change.
    """

    __tablename__: str = "workflow_versions"

    workflow_id: Mapped[UuidType] = mapped_column(
        PgUuidCol(as_uuid=True),
        ForeignKey("workflows.id", ondelete="CASCADE"),
    )
    version: Mapped[int] = mapped_column(nullable=False)
    definition: Mapped[JsonDict] = mapped_column(PgJsonb, nullable=False)
    change_summary: Mapped[str | None] = mapped_column(String(1000))

    # Relationships
    workflow: Mapped[DBWorkflow] = relationship(
        passive_deletes=True, lazy="raise_on_sql"
    )

    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint(
            "workflow_id", "version", name="uq_workflow_versions_workflow_version"
        ),
        Index("ix_workflow_versions_workflow_id", "workflow_id"),
    )


class DBWorkflowRun(DatabaseModel, TimestampMixin):
    """Persisted record of a single workflow execution.

    Stores the frozen definition snapshot and execution status/metrics.
    Each run has child node records tracking per-node lifecycle.
    """

    __tablename__: str = "workflow_runs"

    workflow_id: Mapped[UuidType] = mapped_column(
        PgUuidCol(as_uuid=True),
        ForeignKey("workflows.id", ondelete="CASCADE"),
    )
    # Per-workflow sequential run number (1, 2, 3 …) — the human-facing run
    # identity the UI shows instead of the UUID. Assigned at create_run as
    # MAX(run_number)+1 for the workflow (migration 027). The server_default="0"
    # matches the migration (deploy-window safety for the prior release) and keeps
    # autogenerate from flagging drift; create_run always sets the real value.
    run_number: Mapped[int] = mapped_column(nullable=False, server_default=text("0"))
    # SSE registry's queue key. Lets the snapshot endpoint resolve
    # operation_id -> run row without the in-memory registry, so a
    # restarted Fly machine still answers /operations/{id}/snapshot.
    operation_id: Mapped[str | None] = mapped_column(
        String(36),
        unique=True,
        index=True,
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    definition_snapshot: Mapped[JsonDict] = mapped_column(PgJsonb, nullable=False)
    definition_version: Mapped[int] = mapped_column(default=1)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None]
    output_track_count: Mapped[int | None]
    output_playlist_id: Mapped[UuidType | None] = mapped_column(
        PgUuidCol(as_uuid=True), nullable=True
    )
    error_message: Mapped[str | None] = mapped_column(String(2000))
    # Serialized track summaries (track_id, title, artists, rank, metrics) —
    # see serialize_output_tracks() in application/use_cases/workflow_runs.py.
    # That builder stringifies UUIDs and ISO-formats datetimes for the
    # benefit of in-process consumers (preview, CLI). orjson is wired in
    # as the psycopg JSONB dumper at engine init (db_connection.py), so
    # any raw UUID/datetime values that reach the write path are also
    # serialized natively.
    output_tracks: Mapped[list[dict[str, object]] | None] = mapped_column(
        PgJsonb, nullable=True
    )
    # Provenance: the schedule that fired this run, if any. ON DELETE SET NULL
    # so deleting a schedule preserves its historical runs (migration 026).
    triggered_by_schedule_id: Mapped[UuidType | None] = mapped_column(
        PgUuidCol(as_uuid=True),
        ForeignKey("schedules.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Relationships
    workflow: Mapped[DBWorkflow] = relationship(
        passive_deletes=True, lazy="raise_on_sql"
    )
    nodes: Mapped[list[DBWorkflowRunNode]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise_on_sql",
    )

    __table_args__: tuple[SchemaItem, ...] = (
        Index("ix_workflow_runs_workflow_id_started_at", "workflow_id", "started_at"),
        Index("ix_workflow_runs_status", "status"),
        # At most one active (pending/running) run per workflow — the DB-backed
        # concurrency guard. Mirrors migration 024; declared here because
        # integration tests build the schema via metadata.create_all, bypassing
        # the migration chain. The repository maps this constraint's
        # IntegrityError to WorkflowAlreadyRunningError (409).
        Index(
            "uq_workflow_runs_active",
            "workflow_id",
            unique=True,
            postgresql_where=text("status IN ('pending', 'running')"),
        ),
    )


class DBWorkflowRunNode(DatabaseModel):
    """Per-node execution record within a workflow run."""

    __tablename__: str = "workflow_run_nodes"

    run_id: Mapped[UuidType] = mapped_column(
        PgUuidCol(as_uuid=True),
        ForeignKey("workflow_runs.id", ondelete="CASCADE"),
    )
    node_id: Mapped[str] = mapped_column(String(100), nullable=False)
    node_type: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int] = mapped_column(default=0)
    input_track_count: Mapped[int | None]
    output_track_count: Mapped[int | None]
    error_message: Mapped[str | None] = mapped_column(String(2000))
    execution_order: Mapped[int] = mapped_column(default=0)
    # Per-node observation payload (e.g., destination playlist_changes summary).
    # The canonical producer is build_playlist_changes in
    # use_cases/_shared/playlist_results.py, which stringifies UUIDs at the
    # boundary for in-process consumers. orjson is wired in as the
    # psycopg JSONB dumper at engine init (db_connection.py), so a new
    # producer that forgets the rule and emits raw UUIDs / datetimes
    # won't crash at flush time.
    node_details: Mapped[dict[str, object] | None] = mapped_column(
        PgJsonb, nullable=True
    )

    # Relationships
    run: Mapped[DBWorkflowRun] = relationship(
        back_populates="nodes",
        passive_deletes=True,
        lazy="raise_on_sql",
    )

    __table_args__: tuple[SchemaItem, ...] = (
        Index("ix_workflow_run_nodes_run_id", "run_id"),
    )
