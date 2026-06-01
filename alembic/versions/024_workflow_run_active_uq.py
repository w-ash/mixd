"""Enforce one active workflow run per workflow via a partial unique index.

Replaces the in-process concurrency guard (``application/workflows/run_guard.py``,
a process-local ``set``) with a database constraint that holds across every
instance of a multi-machine deploy. A partial unique index on ``workflow_id``
covering only the active statuses lets at most one ``pending``/``running`` run
exist per workflow while leaving terminal rows (completed/failed/cancelled/
crashed) unconstrained — so history accumulates freely and finishing a run frees
the slot.

The run repository maps this index's ``IntegrityError`` (by constraint name) to
``WorkflowAlreadyRunningError`` → HTTP 409. Note the keying change: the old guard
keyed on the ``WorkflowDef`` slug (the JSON filename stem, which clones can
share); this keys on the per-row ``workflow_id`` UUID, which is the correct unit.

PROD NOTE: ``upgrade`` first crashes out any pre-existing active rows so the
unique index can be created without collision. On a healthy DB there are none;
a row stuck ``running`` from a hard crash is exactly what should become
``crashed`` anyway.

Revision ID: 024_workflow_run_active_uq
Revises: 023_drop_workflow_template_kind
Create Date: 2026-05-31
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "024_workflow_run_active_uq"
down_revision: str | None = "023_drop_workflow_template_kind"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Crash out lingering active rows so the partial unique index can't collide
    # on creation (a row left 'running' by a hard crash is a crash by definition).
    op.execute(
        sa.text(
            "UPDATE workflow_runs SET status = 'crashed', completed_at = now() "
            "WHERE status IN ('pending', 'running')"
        )
    )
    op.create_index(
        "uq_workflow_runs_active",
        "workflow_runs",
        ["workflow_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'running')"),
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("uq_workflow_runs_active", table_name="workflow_runs")
