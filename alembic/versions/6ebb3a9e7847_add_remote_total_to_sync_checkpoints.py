"""add remote_total to sync_checkpoints

Revision ID: 6ebb3a9e7847
Revises: 015_child_table_rls
Create Date: 2026-04-07 21:05:31.066191

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6ebb3a9e7847"
down_revision: str | None = "015_child_table_rls"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add remote_total column to sync_checkpoints."""
    op.add_column(
        "sync_checkpoints", sa.Column("remote_total", sa.Integer(), nullable=True)
    )


def downgrade() -> None:
    """Remove remote_total column from sync_checkpoints."""
    op.drop_column("sync_checkpoints", "remote_total")
