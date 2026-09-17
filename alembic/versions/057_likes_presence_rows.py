"""Make ``track_likes`` a presence table.

A liked track is liked or it is not. ``is_liked = false`` was never written
by any code path (measured 2026-09-15): the tombstone existed only so that a
removal could one day be pushed to a service, and it never was. ``last_synced``
was only ever read by the track-merge conflict rule, which now has nothing to
compare — a merge conflict is simply both tracks liked on the same service,
and the winner keeps its row.

Any tombstone rows are deleted before the column goes so that no row can
turn into a like by losing its flag. The DELETE is unconditional; on
production it is expected to match nothing.

Plain DDL: the release command runs over the Neon pooler URL, where
``autocommit_block`` cannot run (see 049 and 051).

Revision ID: 057_likes_presence_rows
Revises: 056_drop_user_id_defaults
Create Date: 2026-09-17
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "057_likes_presence_rows"
down_revision: str | None = "056_drop_user_id_defaults"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Created by 001_initial_schema as ``op.create_index(None, ...)`` under the
# ``ix_%(table_name)s_%(column_0_name)s`` naming convention.
_SERVICE_IS_LIKED_INDEX = "ix_track_likes_service"


def upgrade() -> None:
    op.execute("DELETE FROM track_likes WHERE NOT is_liked")
    op.drop_index(_SERVICE_IS_LIKED_INDEX, table_name="track_likes")
    op.drop_column("track_likes", "is_liked")
    op.drop_column("track_likes", "last_synced")


def downgrade() -> None:
    op.add_column(
        "track_likes",
        sa.Column(
            "is_liked",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    op.add_column(
        "track_likes",
        sa.Column("last_synced", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(_SERVICE_IS_LIKED_INDEX, "track_likes", ["service", "is_liked"])
