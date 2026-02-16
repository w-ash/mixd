"""Add confidence_evidence column to track_mappings table.

This migration adds a JSON column to store detailed matching evidence for
better debugging and analysis of match quality.

Usage:
    alembic upgrade head
"""

import sqlalchemy as sa

from alembic import op

# Target table
target_table = "track_mappings"
new_column = "confidence_evidence"

# Revision identifiers
revision = "d45a90f8a123"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add confidence_evidence column to track_mappings table."""
    # Use JSON type (will use sqlite JSON for sqlite and JSONB for postgres)
    op.add_column(target_table, sa.Column(new_column, sa.JSON))


def downgrade() -> None:
    """Remove confidence_evidence column from track_mappings table."""
    op.drop_column(target_table, new_column)
