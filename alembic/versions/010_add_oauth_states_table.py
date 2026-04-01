"""Add oauth_states table for durable CSRF state + callback user association.

Replaces the in-memory _csrf_states dict with a PostgreSQL table so OAuth
state survives Fly.io VM restarts and associates user_id with each flow.
Short-lived rows (5-minute TTL) are pruned on each new state creation.

Revision ID: 010_add_oauth_states_table
Revises: 009_add_track_version
Create Date: 2026-03-31
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "010_add_oauth_states_table"
down_revision: str | None = "009_add_track_version"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "oauth_states",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("state", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("service", sa.String(32), nullable=False),
        sa.Column("code_verifier", sa.String(), nullable=True),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_oauth_states")),
        sa.UniqueConstraint("state", name=op.f("uq_oauth_states_state")),
    )


def downgrade() -> None:
    op.drop_table("oauth_states")
