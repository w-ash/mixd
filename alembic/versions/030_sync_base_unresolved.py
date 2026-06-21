"""Per-link sync base + first-class unresolved playlist entries (v0.8.7).

Two foundations for safe import/sync:

1. ``playlist_sync_bases`` — one row per link recording the external item set
   (+ snapshot id) the link last reconciled to. Diff/safety/change-detection
   compare freshly fetched remote state against THIS trustworthy, user-scoped
   base instead of the global ``connector_playlists`` cache.

2. ``playlist_tracks`` gains UNRESOLVED membership rows: ``track_id`` becomes
   nullable, with a new (nullable, best-effort) ``connector_track_id`` re-resolution
   FK + an ``unresolved_metadata`` display snapshot, guarded by a CHECK that every
   row is resolved OR carries a display snapshot. An imported playlist is therefore
   always complete (right count + order) — even Spotify local/unavailable tracks
   with no connector_tracks row — so a position can never be a silent hole.

Revision id kept short: alembic_version.version_num is varchar(32).

Revision ID: 030_sync_base_unresolved
Revises: 029_last_sync_unmatched
Create Date: 2026-06-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "030_sync_base_unresolved"
down_revision: str | None = "029_last_sync_unmatched"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Per-link sync base table.
    op.create_table(
        "playlist_sync_bases",
        sa.Column(
            "id",
            pg.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            sa.String(),
            nullable=False,
            server_default="default",
        ),
        sa.Column("link_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("connector_name", sa.String(32), nullable=False),
        sa.Column("connector_playlist_identifier", sa.String(), nullable=False),
        sa.Column("base_snapshot_id", sa.String(64), nullable=True),
        sa.Column("base_taken_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["link_id"],
            ["playlist_mappings.id"],
            name="fk_playlist_sync_bases_link_id_playlist_mappings",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("link_id", name="uq_playlist_sync_bases_link"),
    )
    op.create_index("ix_playlist_sync_bases_user", "playlist_sync_bases", ["user_id"])

    # RLS: user-scoped child table (mirrors migration 015's pattern).
    op.execute(sa.text("ALTER TABLE playlist_sync_bases ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE playlist_sync_bases FORCE ROW LEVEL SECURITY"))
    op.execute(
        sa.text(
            "CREATE POLICY user_isolation ON playlist_sync_bases FOR ALL "
            "USING (user_id = current_setting('app.user_id', TRUE))"
        )
    )

    # 2. Unresolved playlist entries on playlist_tracks.
    op.alter_column("playlist_tracks", "track_id", nullable=True)
    op.add_column(
        "playlist_tracks",
        sa.Column("connector_track_id", pg.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "playlist_tracks",
        sa.Column("unresolved_metadata", pg.JSONB(), nullable=True),
    )
    op.create_foreign_key(
        "fk_playlist_tracks_connector_track_id_connector_tracks",
        "playlist_tracks",
        "connector_tracks",
        ["connector_track_id"],
        ["id"],
        ondelete="SET NULL",
    )
    # Bare token name: the ck naming convention (ck_%(table_name)s_%(constraint_name)s)
    # wraps it into ck_playlist_tracks_resolved_or_source, matching what the ORM's
    # CheckConstraint(name="resolved_or_source") produces. Passing the full name
    # here double-prefixes it.
    op.create_check_constraint(
        "resolved_or_source",
        "playlist_tracks",
        "track_id IS NOT NULL OR unresolved_metadata IS NOT NULL",
    )
    op.create_index(
        "ix_playlist_tracks_unresolved",
        "playlist_tracks",
        ["playlist_id"],
        postgresql_where=sa.text("track_id IS NULL"),
    )


def downgrade() -> None:
    # Reverse of upgrade. Re-tightening track_id to NOT NULL will fail if any
    # unresolved rows exist — expected for a clean-break downgrade.
    op.drop_index("ix_playlist_tracks_unresolved", table_name="playlist_tracks")
    # Bare token name — drop_constraint applies the same ck convention wrapping.
    op.drop_constraint("resolved_or_source", "playlist_tracks", type_="check")
    op.drop_constraint(
        "fk_playlist_tracks_connector_track_id_connector_tracks",
        "playlist_tracks",
        type_="foreignkey",
    )
    op.drop_column("playlist_tracks", "unresolved_metadata")
    op.drop_column("playlist_tracks", "connector_track_id")
    op.alter_column("playlist_tracks", "track_id", nullable=False)

    op.execute(sa.text("DROP POLICY IF EXISTS user_isolation ON playlist_sync_bases"))
    op.drop_index("ix_playlist_sync_bases_user", table_name="playlist_sync_bases")
    op.drop_table("playlist_sync_bases")
