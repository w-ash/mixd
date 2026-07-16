"""Add the pending_actions table — durable two-phase write confirmations.

v0.9.5: the pending-action store moves from an in-process dict to Postgres so
a mutation proposed on one production machine is confirmable on another (Fly
``auto_start_machines`` can route the propose and confirm calls to different
machines, and the remote MCP transport makes that routing routine). Claims are
a single conditional ``DELETE … RETURNING``, so exactly one machine can win a
confirmation token. Rows are short-lived — 5-minute TTL, evicted
opportunistically on every create — so the table stays a handful of rows.

NO RLS policy — deliberately, following the ``chat_feedback`` precedent
(migration 036): the store's error contract distinguishes "someone else's
action" (``ForbiddenError``) from "expired" (``ActionExpiredError``), which
requires *seeing* the owner of a foreign row — RLS invisibility would collapse
the two into "expired". Per-user isolation is enforced by explicit ``user_id``
predicates in every store query instead; the table is reachable only through
``PostgresPendingActionStore``.

Revision ID: 038_pending_actions
Revises: 037_operation_run_initiated_by
Create Date: 2026-07-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "038_pending_actions"
down_revision: str | None = "037_operation_run_initiated_by"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pending_actions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("tool_name", sa.String(), nullable=False),
        sa.Column("tool_input", postgresql.JSONB(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("details", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pending_actions")),
    )


def downgrade() -> None:
    op.drop_table("pending_actions")
