"""Add the play_sources membership table (v0.10.0 projection pipeline).

One row per ledger observation, pointing at the canonical play it backs.
Materializing membership makes projection idempotence a mechanical no-op diff
(an unchanged group produces zero writes) and preserves provenance without
parsing ``track_plays.context``. UNIQUE(user_id, connector_play_id): an
observation contributes to exactly one canonical play; re-projection repoints
via upsert instead of duplicating.

Both FKs cascade: deleting a canonical play or a ledger row must never leave
a dangling membership edge (the diff-apply rebuilds edges from the ledger).

Revision ID: 042_play_sources
Revises: 041_unresolved_partial_index
Create Date: 2026-07-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "042_play_sources"
down_revision: str | None = "041_unresolved_partial_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "play_sources"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.String(), server_default="default", nullable=False),
        sa.Column("track_play_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("connector_play_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_play_sources")),
        sa.ForeignKeyConstraint(
            ["track_play_id"],
            ["track_plays.id"],
            name=op.f("fk_play_sources_track_play_id_track_plays"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["connector_play_id"],
            ["connector_plays.id"],
            name=op.f("fk_play_sources_connector_play_id_connector_plays"),
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "user_id",
            "connector_play_id",
            name="uq_play_sources_connector_play",
        ),
    )
    op.create_index("ix_play_sources_track_play", _TABLE, ["track_play_id"])
    # The connector_play_id CASCADE FK needs its own index: the unique
    # constraint leads with user_id, which cannot serve the FK-enforcement
    # probe, so connector_plays deletes would seq-scan this table.
    op.create_index("ix_play_sources_connector_play", _TABLE, ["connector_play_id"])

    op.execute(sa.text(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY"))
    op.execute(
        sa.text(
            f"CREATE POLICY user_isolation ON {_TABLE} FOR ALL "
            f"USING (user_id = current_setting('app.user_id', TRUE))"
        )
    )


def downgrade() -> None:
    op.execute(sa.text(f"DROP POLICY IF EXISTS user_isolation ON {_TABLE}"))
    op.execute(sa.text(f"ALTER TABLE {_TABLE} DISABLE ROW LEVEL SECURITY"))
    op.drop_index("ix_play_sources_connector_play", table_name=_TABLE)
    op.drop_index("ix_play_sources_track_play", table_name=_TABLE)
    op.drop_table(_TABLE)
