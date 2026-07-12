"""Add ``initiated_by`` to ``operation_runs`` (v0.9.1 agent-activity attribution).

Records who initiated a long-running operation — "manual" (the user, default),
"assistant" (an AI-agent-launched background op), or "schedule" — so an
assistant-initiated run is visibly attributed in the same run log a human uses.
Trusting the agent then never requires trusting one's memory of the chat.

``String(16)``, non-null, server default ``"manual"``: existing rows and every
current caller read as user-initiated, so this is fully backward-compatible. The
chat→launcher wiring sets "assistant" going forward.

Revision ID: 037_operation_run_initiated_by
Revises: 036_add_chat_feedback
Create Date: 2026-07-11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "037_operation_run_initiated_by"
down_revision: str | None = "036_add_chat_feedback"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "operation_runs",
        sa.Column(
            "initiated_by",
            sa.String(length=16),
            nullable=False,
            server_default="manual",
        ),
    )


def downgrade() -> None:
    op.drop_column("operation_runs", "initiated_by")
