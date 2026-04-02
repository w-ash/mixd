"""Purge orphaned user_id='default' data.

Early dev clean break — users start fresh with real Neon Auth identities.
After FORCE RLS (011), default-user data is invisible to authenticated users
anyway; this migration removes the orphaned rows.

Delete order respects foreign key dependencies (children before parents).
Not reversible — data deletion is permanent.

Revision ID: 012_purge_default_user_data
Revises: 011_force_rls
"""

import sqlalchemy as sa

from alembic import op

revision: str = "012_purge_default_user_data"
down_revision: str | None = "011_force_rls"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None

# Ordered by FK dependencies: children first, then parents.
# track_likes/track_plays/track_mappings/match_reviews → tracks
# playlists (playlist_tracks cascade)
# workflows (workflow_runs/versions cascade)
TABLES_IN_DELETE_ORDER = [
    "sync_checkpoints",
    "oauth_tokens",
    "user_settings",
    "connector_plays",
    "track_likes",
    "track_plays",
    "match_reviews",
    "track_mappings",
    "workflows",
    "playlists",
    "tracks",
]


def upgrade() -> None:
    """Delete all rows with user_id='default' from user-scoped tables."""
    for table in TABLES_IN_DELETE_ORDER:
        op.execute(sa.text(f"DELETE FROM {table} WHERE user_id = 'default'"))


def downgrade() -> None:
    """Data deletion is not reversible."""
