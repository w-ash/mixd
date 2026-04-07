"""Add user_id + RLS to track_metrics and playlist_mappings.

Defense-in-depth for multi-user: these child tables were previously
scoped only through parent FK relationships (tracks, playlists).
Adding direct user_id columns + RLS policies ensures isolation even
if queried independently.

Backfills user_id from the parent table via FK before enforcing NOT NULL.

Revision ID: 015_child_table_rls
Revises: 014_playlist_tracks_fk_idx
Create Date: 2026-04-06
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "015_child_table_rls"
down_revision: str = "014_playlist_tracks_fk_idx"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = [
    {"table": "track_metrics", "parent": "tracks", "fk_col": "track_id"},
    {"table": "playlist_mappings", "parent": "playlists", "fk_col": "playlist_id"},
]


def upgrade() -> None:
    for t in _TABLES:
        table = t["table"]
        parent = t["parent"]
        fk_col = t["fk_col"]

        # Add nullable user_id first (backfill before enforcing NOT NULL)
        op.add_column(table, sa.Column("user_id", sa.String(), nullable=True))

        # Backfill from parent table
        op.execute(
            sa.text(
                f"UPDATE {table} c SET user_id = p.user_id "
                f"FROM {parent} p WHERE c.{fk_col} = p.id"
            )
        )

        # Set remaining NULLs (orphaned rows) to default
        op.execute(
            sa.text(f"UPDATE {table} SET user_id = 'default' WHERE user_id IS NULL")
        )

        # Enforce NOT NULL + default
        op.alter_column(table, "user_id", nullable=False, server_default="default")

        # Enable RLS
        op.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
        op.execute(
            sa.text(
                f"CREATE POLICY user_isolation ON {table} FOR ALL "
                f"USING (user_id = current_setting('app.user_id', TRUE))"
            )
        )


def downgrade() -> None:
    for t in reversed(_TABLES):
        table = t["table"]
        op.execute(sa.text(f"DROP POLICY IF EXISTS user_isolation ON {table}"))
        op.execute(sa.text(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY"))
        op.drop_column(table, "user_id")
