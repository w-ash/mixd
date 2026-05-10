"""Add heartbeat_at to workflow_runs for stale-run detection.

Production runs can stall mid-execution (Prefect ephemeral cold-start, OOM,
event-loop block) leaving rows in ``status='running'`` indefinitely with no
error. A periodic ticker bumps ``heartbeat_at`` while a run is alive; a
sweeper marks rows ``failed`` when the heartbeat goes silent past 60s.

Revision ID: 020_workflow_run_heartbeat
Revises: 019_output_playlist_id_uuid
Create Date: 2026-05-09
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "020_workflow_run_heartbeat"
down_revision: str | None = "019_output_playlist_id_uuid"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workflow_runs",
        sa.Column(
            "heartbeat_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("workflow_runs", "heartbeat_at")
