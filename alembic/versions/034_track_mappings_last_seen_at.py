"""Add last_seen_at to track_mappings.

Re-encountering a mapped connector track during import proves the connector
track still exists, not that the canonical match is right (v0.8.18 FM1a) —
so re-encounters stamp this column instead of overwriting confidence.
NULL means "not re-encountered since v0.8.18 shipped"; no backfill.

Revision ID: 034_track_mappings_last_seen_at
Revises: 033_rename_day_window_keys
"""

import sqlalchemy as sa

from alembic import op

revision: str = "034_track_mappings_last_seen_at"
down_revision: str | None = "033_rename_day_window_keys"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    """Add the nullable freshness column."""
    op.add_column(
        "track_mappings",
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Drop the freshness column."""
    op.drop_column("track_mappings", "last_seen_at")
