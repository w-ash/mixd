"""Add ``operation_id`` to ``operation_runs`` (v0.8.7 operation-awareness).

The SSE ``operation_id`` (the live queue key) was only persisted on
``workflow_runs`` — so the general snapshot/active-operations endpoints for
import/sync operations had no way to resolve an ``operation_id`` back to its
audit row. This adds the same nullable, unique, indexed column to
``operation_runs`` so ``GET /operations/{operation_id}/run-snapshot`` and
``GET /operations/active`` (which must hand the frontend an ``operation_id`` to
re-attach the SSE stream) work for every operation type, not just workflows.

Nullable + unique: existing rows keep NULL (Postgres treats NULLs as distinct in
a unique index), new rows get the operation_id set at kickoff by ``start_run``.

Revision ID: 031_operation_run_operation_id
Revises: 030_sync_base_unresolved
Create Date: 2026-06-21
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "031_operation_run_operation_id"
down_revision: str | None = "030_sync_base_unresolved"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "operation_runs",
        sa.Column("operation_id", sa.String(length=36), nullable=True),
    )
    op.create_index(
        "ix_operation_runs_operation_id",
        "operation_runs",
        ["operation_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_operation_runs_operation_id", table_name="operation_runs")
    op.drop_column("operation_runs", "operation_id")
