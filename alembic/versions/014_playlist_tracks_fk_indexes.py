"""Add indexes on playlist_tracks foreign keys.

The playlist_tracks junction table has foreign keys to playlists and tracks
but no indexes on the referencing columns. This causes sequential scans on
JOIN queries and cascade deletes. Found by Neon Data API Advisors scan.

Revision ID: 014_playlist_tracks_fk_idx
Revises: 013_shared_templates
Create Date: 2026-04-06
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "014_playlist_tracks_fk_idx"
down_revision: str = "013_shared_templates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_playlist_tracks_playlist_id",
        "playlist_tracks",
        ["playlist_id"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_playlist_tracks_track_id",
        "playlist_tracks",
        ["track_id"],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_playlist_tracks_track_id", table_name="playlist_tracks", if_exists=True
    )
    op.drop_index(
        "ix_playlist_tracks_playlist_id", table_name="playlist_tracks", if_exists=True
    )
