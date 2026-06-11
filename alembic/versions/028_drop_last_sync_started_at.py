"""Drop playlist_mappings.last_sync_started_at to match the ORM model (v0.8.4).

The chore/tighten-codebase dead-code purge removed the column from
``DBPlaylistMapping`` and its only writer (``update_sync_status`` stopped
stamping it on the SYNCING transition) — nothing ever read it back. Without
this migration the model and the migration-built schema disagree, so
``alembic check`` fails and the next ``--autogenerate`` would smuggle the
drop into an unrelated migration.

Downgrade restores the column shape only; the stamped timestamps are gone.

Revision ID: 028_drop_last_sync_started_at
Revises: 027_add_workflow_run_number
Create Date: 2026-06-11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "028_drop_last_sync_started_at"
down_revision: str | None = "027_add_workflow_run_number"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("playlist_mappings", "last_sync_started_at")


def downgrade() -> None:
    op.add_column(
        "playlist_mappings",
        sa.Column("last_sync_started_at", sa.DateTime(timezone=True), nullable=True),
    )
