"""Add composite indexes for keyset pagination on tracks table.

Keyset pagination uses WHERE (sort_col, id) > (:value, :last_id) instead of
OFFSET. Composite indexes on (sort_col, id) let PostgreSQL satisfy these
row-value comparisons with a single index scan.

- (created_at, id) for "added_desc" / "added_asc" sort
- (artists_text, id) for "artist_asc" / "artist_desc" sort
- title already has a GIN trigram index; B-tree (title, id) added for keyset
- duration_ms sort is rare; skipped to avoid unnecessary index overhead

Revision ID: 003_keyset_idx
Revises: 002_pg_opt
Create Date: 2026-03-18
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "003_keyset_idx"
down_revision: str = "002_pg_opt"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_tracks_title_id", "tracks", ["title", "id"], if_not_exists=True)
    op.create_index(
        "ix_tracks_created_at_id",
        "tracks",
        ["created_at", "id"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_tracks_artists_text_id",
        "tracks",
        ["artists_text", "id"],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("ix_tracks_artists_text_id", table_name="tracks", if_exists=True)
    op.drop_index("ix_tracks_created_at_id", table_name="tracks", if_exists=True)
    op.drop_index("ix_tracks_title_id", table_name="tracks", if_exists=True)
