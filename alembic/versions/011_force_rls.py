"""Enable FORCE ROW LEVEL SECURITY on all user-scoped tables.

Without FORCE, the table owner role (which the application uses on Neon)
bypasses RLS policies. FORCE makes RLS apply even to the table owner,
completing the defense-in-depth safety net alongside the repository-level
WHERE clause filtering.

Revision ID: 011_force_rls
Revises: 010_add_oauth_states_table
"""

import sqlalchemy as sa

from alembic import op

revision: str = "011_force_rls"
down_revision: str | None = "010_add_oauth_states_table"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None

# All 11 tables with RLS policies (from migration 007)
RLS_TABLES = [
    "tracks",
    "track_mappings",
    "match_reviews",
    "track_likes",
    "track_plays",
    "connector_plays",
    "playlists",
    "workflows",
    "oauth_tokens",
    "user_settings",
    "sync_checkpoints",
]


def upgrade() -> None:
    """Force RLS on all user-scoped tables — table owner no longer bypasses."""
    for table in RLS_TABLES:
        op.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))


def downgrade() -> None:
    """Remove FORCE — table owner bypasses RLS again."""
    for table in RLS_TABLES:
        op.execute(sa.text(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY"))
