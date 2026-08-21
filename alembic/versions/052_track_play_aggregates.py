"""Denormalized play aggregates on tracks (v0.10.4).

The library's play sorts must stay index scans at any history size, so
``play_count`` / ``first_played_at`` / ``last_played_at`` live on the track
row. ``recompute_track_play_aggregates`` is their only writer.

Revision ID: 052_track_play_aggregates
Revises: 051_track_mappings_fk_restrict
Create Date: 2026-08-20
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "052_track_play_aggregates"
down_revision: str | None = "051_track_mappings_fk_restrict"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Joined on user_id too, so a mistenanted play (the v0.10.3 class) cannot
# leak into another tenant's aggregate.
_BACKFILL = sa.text("""
    UPDATE tracks t
    SET play_count = agg.play_count,
        first_played_at = agg.first_played_at,
        last_played_at = agg.last_played_at
    FROM (
        SELECT track_id,
               user_id,
               COUNT(*) AS play_count,
               MIN(played_at) AS first_played_at,
               MAX(played_at) AS last_played_at
        FROM track_plays
        GROUP BY track_id, user_id
    ) AS agg
    WHERE t.id = agg.track_id
      AND t.user_id = agg.user_id
""")

# RLS bracket (precedent: 035/040/050): 'app.user_id' is unset under Alembic,
# so without it the backfill matches zero rows on a non-BYPASSRLS owner.
_RLS_TABLES = ("tracks", "track_plays")


def upgrade() -> None:
    op.add_column(
        "tracks",
        sa.Column("play_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "tracks",
        sa.Column("last_played_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "tracks",
        sa.Column("first_played_at", sa.DateTime(timezone=True), nullable=True),
    )

    for table in _RLS_TABLES:
        op.execute(sa.text(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY"))
    try:
        _ = op.get_bind().execute(_BACKFILL)
    finally:
        for table in _RLS_TABLES:
            op.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))

    op.create_index(
        "ix_tracks_user_last_played",
        "tracks",
        ["user_id", sa.text("last_played_at DESC NULLS LAST")],
    )
    op.create_index("ix_tracks_user_play_count", "tracks", ["user_id", "play_count"])


def downgrade() -> None:
    op.drop_index("ix_tracks_user_play_count", table_name="tracks")
    op.drop_index("ix_tracks_user_last_played", table_name="tracks")
    op.drop_column("tracks", "first_played_at")
    op.drop_column("tracks", "last_played_at")
    op.drop_column("tracks", "play_count")
