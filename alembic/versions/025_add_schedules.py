"""Add the schedules table for v0.8.2 workflow/sync scheduling.

A schedule fires a workflow run or a background sync on a simple calendar
cadence — **daily** at ``hour:minute`` or **weekly** on a ``day_of_week`` at
``hour:minute`` (minute granularity) — in the user's IANA timezone. The
'daily'/'weekly' kind is derived from whether ``day_of_week`` is set, not stored.
Exactly one target is set: ``workflow_id`` (FK, CASCADE) XOR ``sync_target``
(a free-text "service:entity" key validated in the application layer — kept
TEXT so adding a connector is a one-line Literal edit, not a migration).

NO RLS policy — deliberately, like ``workflow_runs`` (the table the run sweeper
already reads cross-tenant). Per-user isolation is enforced by an explicit
``WHERE user_id`` in every CRUD repository method, while the scheduler's
cross-tenant poll (``find_due_schedules``) reads every user's due rows. The
dedicated-BYPASSRLS-role design is the documented upgrade path for multi-instance.

The CHECK constraints (exclusive arc, time-of-day range, day-of-week range,
status, non-negative counters) encode the entity invariants at the DB level. They cannot live in ``db_models.__table_args__`` per the
codebase convention, so the integration suite — which builds the schema via
``metadata.create_all`` — does NOT exercise them; verify upgrade/downgrade/upgrade
on a disposable ``postgres:17-alpine`` before tagging.

Revision ID: 025_add_schedules
Revises: 024_workflow_run_active_uq
Create Date: 2026-06-01
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "025_add_schedules"
down_revision: str | None = "024_workflow_run_active_uq"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "schedules",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False, server_default="default"),
        sa.Column("workflow_id", sa.UUID(), nullable=True),
        sa.Column("sync_target", sa.String(length=64), nullable=True),
        sa.Column("hour", sa.Integer(), nullable=False),
        sa.Column("minute", sa.Integer(), nullable=False),
        sa.Column("day_of_week", sa.Integer(), nullable=True),
        sa.Column(
            "timezone", sa.String(length=64), nullable=False, server_default="UTC"
        ),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default="enabled"
        ),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_status", sa.String(length=32), nullable=True),
        sa.Column("last_error", sa.String(length=2000), nullable=True),
        sa.Column("last_run_id", sa.UUID(), nullable=True),
        sa.Column("run_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "consecutive_failures", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_schedules")),
        sa.ForeignKeyConstraint(
            ["workflow_id"],
            ["workflows.id"],
            name=op.f("fk_schedules_workflow_id_workflows"),
            ondelete="CASCADE",
        ),
        # Exclusive arc: exactly one target is set.
        sa.CheckConstraint(
            "num_nonnulls(workflow_id, sync_target) = 1",
            name=op.f("ck_schedules_target_xor"),
        ),
        # Wall-clock cadence ranges (minute granularity).
        sa.CheckConstraint(
            "hour BETWEEN 0 AND 23 AND minute BETWEEN 0 AND 59",
            name=op.f("ck_schedules_time_of_day"),
        ),
        # day_of_week: NULL means daily; 0 (Sunday) to 6 (Saturday) means weekly.
        sa.CheckConstraint(
            "day_of_week IS NULL OR day_of_week BETWEEN 0 AND 6",
            name=op.f("ck_schedules_day_of_week"),
        ),
        sa.CheckConstraint(
            "status IN ('enabled', 'disabled')",
            name=op.f("ck_schedules_valid_status"),
        ),
        sa.CheckConstraint(
            "run_count >= 0 AND consecutive_failures >= 0",
            name=op.f("ck_schedules_counts_nonneg"),
        ),
    )
    op.create_index("ix_schedules_user_id", "schedules", ["user_id"])
    # Poll hot path: WHERE status='enabled' AND next_run_at <= now().
    op.create_index(
        "ix_schedules_status_next_run_at",
        "schedules",
        ["status", "next_run_at"],
    )
    # Reaper hot path: WHERE started_at IS NOT NULL AND started_at < threshold.
    # Partial so only in-flight claims (a small set) are indexed.
    op.create_index(
        "ix_schedules_started_at",
        "schedules",
        ["started_at"],
        postgresql_where=sa.text("started_at IS NOT NULL"),
    )
    # One workflow-schedule per (user, workflow); one sync-schedule per
    # (user, sync_target). Partial — the unused arm is NULL for the other type.
    op.create_index(
        "uq_schedules_workflow_target",
        "schedules",
        ["user_id", "workflow_id"],
        unique=True,
        postgresql_where=sa.text("workflow_id IS NOT NULL"),
    )
    op.create_index(
        "uq_schedules_sync_target",
        "schedules",
        ["user_id", "sync_target"],
        unique=True,
        postgresql_where=sa.text("sync_target IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_schedules_sync_target", table_name="schedules")
    op.drop_index("uq_schedules_workflow_target", table_name="schedules")
    op.drop_index("ix_schedules_started_at", table_name="schedules")
    op.drop_index("ix_schedules_status_next_run_at", table_name="schedules")
    op.drop_index("ix_schedules_user_id", table_name="schedules")
    op.drop_table("schedules")
