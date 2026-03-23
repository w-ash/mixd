"""Add oauth_tokens table for database-backed credential persistence.

Stores OAuth 2.0 tokens (Spotify) and session keys (Last.fm) in the
database so authentication survives container restarts on Fly.io.
One row per service via UNIQUE(service), enabling upsert semantics.

Revision ID: 005_oauth_tokens
Revises: 004_check_constraints
Create Date: 2026-03-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "005_oauth_tokens"
down_revision: str = "004_check_constraints"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "oauth_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("service", sa.String(32), nullable=False),
        sa.Column("token_type", sa.String(20), nullable=False),
        sa.Column("access_token", sa.String(), nullable=True),
        sa.Column("refresh_token", sa.String(), nullable=True),
        sa.Column("session_key", sa.String(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scope", sa.String(), nullable=True),
        sa.Column("account_name", sa.String(255), nullable=True),
        sa.Column("extra_data", JSONB, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("service", name="uq_oauth_tokens_service"),
    )


def downgrade() -> None:
    op.drop_table("oauth_tokens")
