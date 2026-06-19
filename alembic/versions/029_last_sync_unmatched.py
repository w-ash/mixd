"""Add playlist_mappings.last_sync_tracks_unmatched (v0.8.6).

Records how many canonical tracks had no connector match on the last push sync
(surfaced to users as "unmatched"). Sits alongside ``last_sync_tracks_added`` /
``last_sync_tracks_removed``. Nullable int, no backfill — pre-existing links read
back ``NULL`` (rendered as no unmatched info) until their next sync.

Revision id kept short: alembic_version.version_num is varchar(32).

Revision ID: 029_last_sync_unmatched
Revises: 028_drop_last_sync_started_at
Create Date: 2026-06-18
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "029_last_sync_unmatched"
down_revision: str | None = "028_drop_last_sync_started_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "playlist_mappings",
        sa.Column("last_sync_tracks_unmatched", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("playlist_mappings", "last_sync_tracks_unmatched")
