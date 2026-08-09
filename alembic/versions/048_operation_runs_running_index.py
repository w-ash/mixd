"""Partial index for the running-rows queries on ``operation_runs``.

Two consumers scan for ``status = 'running'`` rows bounded by ``started_at``:
the startup reaper (``list_running_started_before``) and the pre-deploy busy
probe (``count_running_started_since``, behind ``GET /health?busy=true``).
``operation_runs`` is an append-only audit log that grows without bound, and
the busy probe is auth-exempt — an unauthenticated caller can trigger the
query — so neither may degrade into a full-table scan as history accumulates.
A partial index on ``(started_at) WHERE status = 'running'`` stays tiny
(running rows are bounded by the concurrency cap plus the odd phantom) and
serves both predicates.

The downgrade drops the index; no data is touched in either direction.

Revision ID: 048_operation_runs_running_idx
Revises: 047_operation_run_partial
Create Date: 2026-08-08
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "048_operation_runs_running_idx"
down_revision: str | None = "047_operation_run_partial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX = "ix_operation_runs_running_started_at"
_TABLE = "operation_runs"


def upgrade() -> None:
    op.create_index(
        _INDEX,
        _TABLE,
        ["started_at"],
        postgresql_where=sa.text("status = 'running'"),
    )


def downgrade() -> None:
    op.drop_index(_INDEX, table_name=_TABLE)
