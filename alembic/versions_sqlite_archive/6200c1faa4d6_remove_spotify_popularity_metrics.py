"""remove_spotify_popularity_metrics

Revision ID: 6200c1faa4d6
Revises: 9c0f10992fa0
Create Date: 2026-03-06 13:35:21.529952

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6200c1faa4d6"
down_revision: str | None = "9c0f10992fa0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Delete stale Spotify popularity metric rows (field removed from API Feb 2026)."""
    op.execute("DELETE FROM track_metrics WHERE metric_type = 'spotify_popularity'")
    op.execute("DELETE FROM track_metrics WHERE metric_type = 'popularity'")


def downgrade() -> None:
    """Data deletion is not reversible."""
