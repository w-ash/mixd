"""Per-workflow sequential run number for human-facing run identity (v0.8.4).

The UUID ``id`` is the stable run key (URLs, API, SSE), but it's noise in the
UI — the user thinks "my 5th run of this workflow", not a 36-char hex string.
``run_number`` is that local count: 1 for a workflow's first run, N for its Nth,
assigned at creation as ``MAX(run_number)+1`` for that workflow.

No ``(workflow_id, run_number)`` unique constraint: the existing
``uq_workflow_runs_active`` partial index already permits at most one active
(pending/running) run per workflow, so concurrent creates for one workflow
serialize there (the loser raises ``WorkflowAlreadyRunningError``) — adding a
second unique index would only risk masking that 409 path. Runs are append-only
(no per-run delete; only cascade on workflow delete), so the count never reuses.

Backfill orders existing rows by ``created_at, id`` per workflow so history keeps
its real chronology.

The column ships ``NOT NULL DEFAULT 0``, not bare ``NOT NULL``: Fly runs this as a
``release_command`` *before* switching traffic, so the previous release is still
serving while the column flips to ``NOT NULL``. That old code's ``create_run``
INSERT doesn't know the column and would violate the constraint (a 500 for any run
— notably a scheduled one — created mid-deploy). The ``0`` default keeps the INSERT
valid; new code always assigns the real ``MAX+1``, so ``0`` only ever lands on a run
created by the old release during the cutover window (and ``0`` is already the
domain's "unsaved" sentinel).

Revision ID: 027_add_workflow_run_number
Revises: 026_add_schedule_run_linkage
Create Date: 2026-06-08
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "027_add_workflow_run_number"
down_revision: str | None = "026_add_schedule_run_linkage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # NOT NULL with a server-side default so the still-live previous release can
    # keep inserting workflow_runs during Fly's pre-cutover release_command window
    # (see module docstring). PG ≥11 fills existing rows from the default without a
    # table rewrite; the backfill below then overwrites them with real numbers.
    op.add_column(
        "workflow_runs",
        sa.Column(
            "run_number", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
    )
    # Backfill: number each workflow's runs 1..N by chronology, overwriting the
    # transient 0 default the column was created with.
    op.execute(
        """
        UPDATE workflow_runs AS wr
        SET run_number = sub.rn
        FROM (
            SELECT id,
                   ROW_NUMBER() OVER (
                       PARTITION BY workflow_id
                       ORDER BY created_at, id
                   ) AS rn
            FROM workflow_runs
        ) AS sub
        WHERE wr.id = sub.id
        """
    )


def downgrade() -> None:
    op.drop_column("workflow_runs", "run_number")
