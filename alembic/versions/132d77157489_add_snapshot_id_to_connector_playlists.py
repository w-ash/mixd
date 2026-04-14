"""add snapshot_id to connector_playlists

Revision ID: 132d77157489
Revises: c602c5a08631
Create Date: 2026-04-13 08:29:22.605517

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "132d77157489"
down_revision: str | None = "c602c5a08631"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add snapshot_id column to connector_playlists.

    snapshot_id is Spotify's cheap change-detection token. The import
    use case compares stored vs fetched snapshot_id to short-circuit
    re-import work. Nullable because existing rows were cached before
    snapshot tracking landed — NULL means "no cached snapshot, refetch".
    """
    op.add_column(
        "connector_playlists", sa.Column("snapshot_id", sa.String(64), nullable=True)
    )


def downgrade() -> None:
    """Remove snapshot_id column from connector_playlists."""
    op.drop_column("connector_playlists", "snapshot_id")
