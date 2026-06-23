"""Add ``request_params`` to ``operation_runs`` (v0.8.8 retry-failed foundation).

Records the parameters needed to *re-invoke* an operation — ``connector_name``
and ``sync_direction`` for an import — so "Retry failed only" can reconstruct
the call server-side from the run alone (the failed item identifiers come from
``issues``). The retry toast fires after the user has navigated away, so the
client no longer holds the original request; the row must carry enough to re-run
it.

JSONB, non-null default ``{}`` (matching ``counts`` / ``issues``): existing rows
and operations that record no params read as an empty dict — "not retryable".
Stores only connector config (strings) — never UUIDs or ``user_id``; the retry
route re-derives the owner from auth, never from stored params.

Revision ID: 032_operation_run_request_params
Revises: 031_operation_run_operation_id
Create Date: 2026-06-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "032_operation_run_request_params"
down_revision: str | None = "031_operation_run_operation_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "operation_runs",
        sa.Column(
            "request_params",
            pg.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("operation_runs", "request_params")
