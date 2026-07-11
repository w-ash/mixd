"""Add the chat_feedback table for v0.9.0 workflow-assistant thumbs feedback.

Captures a human thumbs-up/thumbs-down (plus an optional free-text note) on a
workflow definition the chat assistant generated, alongside the prompt that
produced it. This is a write-once, human-only signal — recorded by the
thumbs UI, never by the assistant itself — used to build a labeled corpus for
evaluating and improving generation quality over time.

NO RLS policy — deliberately, following the ``schedules`` precedent (migration
025): per-user isolation is enforced by an explicit ``WHERE user_id`` filter in
every repository method rather than a database policy. There is no
cross-tenant hot-path here (unlike the scheduler's poll), so the exception is
even more conservative than ``schedules`` — but the same "no RLS, explicit
WHERE" pattern is deliberately reused rather than introducing a second
per-user-isolation mechanism into the codebase.

The CHECK constraint on ``signal`` encodes the entity invariant at the DB
level, per the codebase convention (CHECKs live only in the migration, never
in ``db_models.__table_args__``).

Revision ID: 036_add_chat_feedback
Revises: 035_lastfm_identifier_fold
Create Date: 2026-07-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "036_add_chat_feedback"
down_revision: str | None = "035_lastfm_identifier_fold"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chat_feedback",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("generated_workflow_def", postgresql.JSONB(), nullable=False),
        sa.Column("signal", sa.String(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chat_feedback")),
        sa.CheckConstraint(
            "signal IN ('positive', 'negative')",
            name=op.f("ck_chat_feedback_signal"),
        ),
    )
    op.create_index("ix_chat_feedback_user_id", "chat_feedback", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_chat_feedback_user_id", table_name="chat_feedback")
    op.drop_table("chat_feedback")
