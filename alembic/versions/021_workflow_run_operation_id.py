"""Add operation_id to workflow_runs for SSE-state -> run mapping.

The SSE registry uses ``operation_id`` (uuid4) as its queue key while
``workflow_runs.id`` is the persistent run identifier. Today the mapping
is implicit and in-memory. The frontend's REST snapshot fallback (called
when SSE looks stalled) needs to resolve ``operation_id`` to a run row
without help from the in-memory registry, which doesn't survive a Fly
machine restart.

Nullable so pre-existing rows from before this migration remain readable
(they predate the snapshot fallback feature). New rows always carry
``operation_id``.

Revision ID: 021_workflow_run_operation_id
Revises: 020_workflow_run_heartbeat
Create Date: 2026-05-09
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "021_workflow_run_operation_id"
down_revision: str | None = "020_workflow_run_heartbeat"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workflow_runs",
        sa.Column(
            "operation_id",
            sa.String(36),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_workflow_runs_operation_id",
        "workflow_runs",
        ["operation_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_workflow_runs_operation_id", table_name="workflow_runs")
    op.drop_column("workflow_runs", "operation_id")
