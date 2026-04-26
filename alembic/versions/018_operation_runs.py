"""Add operation_runs table for v0.7.7 audit log.

Every long-running SSE operation produces one ``OperationRun`` row written
at kickoff (``status="running"``) and finalized on terminal events. The
seam-level recorder catches playlist imports, likes sync, history import,
the bulk apply-assignments engine, plus playlist sync and workflow runs —
observability for free across every current and future SSE flow.

``counts`` and ``issues`` are JSONB because each operation type defines its
own payload shape (failed track vs. conflict vs. rate-limit skip). The
plan's "rule of 3" for normalization isn't met today.

Revision ID: 018_operation_runs
Revises: 017_rename_assignment
Create Date: 2026-04-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg_dialect

from alembic import op

revision: str = "018_operation_runs"
down_revision: str | None = "017_rename_assignment"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "operation_runs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False, server_default="default"),
        sa.Column("operation_type", sa.String(length=64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "counts",
            pg_dialect.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "issues",
            pg_dialect.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_operation_runs")),
        sa.CheckConstraint(
            "status IN ('running', 'complete', 'error', 'cancelled')",
            name=op.f("ck_operation_runs_valid_status"),
        ),
    )
    # Index supports the list_for_user query: WHERE user_id = ? ORDER BY
    # started_at DESC. Postgres scans ASC indexes in reverse so we don't
    # need a DESC index here.
    op.create_index(
        "ix_operation_runs_user_id_started_at",
        "operation_runs",
        ["user_id", "started_at"],
    )

    # -- RLS policy (matches 015 child-table RLS pattern) --------------------
    op.execute(sa.text("ALTER TABLE operation_runs ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE operation_runs FORCE ROW LEVEL SECURITY"))
    op.execute(
        sa.text(
            "CREATE POLICY user_isolation ON operation_runs FOR ALL "
            "USING (user_id = current_setting('app.user_id', TRUE))"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DROP POLICY IF EXISTS user_isolation ON operation_runs"))
    op.execute(sa.text("ALTER TABLE operation_runs DISABLE ROW LEVEL SECURITY"))
    op.drop_index("ix_operation_runs_user_id_started_at", table_name="operation_runs")
    op.drop_table("operation_runs")
