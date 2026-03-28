"""Add version column to tracks table for optimistic concurrency.

Tracks persistence state: version=0 means unpersisted (application-created),
version≥1 means persisted. Enables the require_database_tracks() pipeline
guard and prepares for optimistic locking in multi-user (v0.6.x).

Revision ID: 009_add_track_version
Revises: 008_uuid_primary_keys
Create Date: 2026-03-26
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "009_add_track_version"
down_revision: str | None = "008_uuid_primary_keys"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add version column with default 1 (all existing rows are persisted)."""
    op.add_column(
        "tracks",
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    """Remove version column."""
    op.drop_column("tracks", "version")
