"""Add playlist_metadata_mappings and playlist_mapping_members tables.

v0.7.4 Epic 1: the mapping model lets the user say "this Spotify playlist
means preference=star" or "this Spotify playlist means tag mood:chill".
Mappings FK to ``connector_playlists`` (not ``playlist_mappings``), so
the user can tag-map a playlist without forking it into a canonical Mixd
Playlist — Epic 7's ConnectorPlaylist-always / canonical-optional design.

``playlist_mapping_members`` captures the per-import membership snapshot
so removal diffs are computable on re-import. It carries a denormalized
``user_id`` column to enable direct-query RLS isolation (matches the 015
child-table RLS pattern).

Revision ID: 016_playlist_metadata_mappings
Revises: f80e19f95cdd
Create Date: 2026-04-16
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "016_playlist_metadata_mappings"
down_revision: str | None = "f80e19f95cdd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ["playlist_metadata_mappings", "playlist_mapping_members"]


def upgrade() -> None:
    # -- playlist_metadata_mappings: one row per (connector playlist, action)
    op.create_table(
        "playlist_metadata_mappings",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.String(), server_default="default", nullable=False),
        sa.Column("connector_playlist_id", sa.UUID(), nullable=False),
        sa.Column("action_type", sa.String(length=16), nullable=False),
        sa.Column("action_value", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["connector_playlist_id"],
            ["connector_playlists.id"],
            name=op.f(
                "fk_playlist_metadata_mappings_connector_playlist_id_connector_playlists"
            ),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_playlist_metadata_mappings")),
        sa.UniqueConstraint(
            "connector_playlist_id",
            "action_type",
            "action_value",
            name="uq_playlist_metadata_mappings_action",
        ),
        sa.CheckConstraint(
            "action_type IN ('set_preference', 'add_tag')",
            name=op.f("ck_playlist_metadata_mappings_valid_action_type"),
        ),
    )
    op.create_index(
        "ix_playlist_metadata_mappings_user_id",
        "playlist_metadata_mappings",
        ["user_id"],
    )

    # -- playlist_mapping_members: per-mapping membership snapshot -----------
    op.create_table(
        "playlist_mapping_members",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.String(), server_default="default", nullable=False),
        sa.Column("mapping_id", sa.UUID(), nullable=False),
        sa.Column("track_id", sa.UUID(), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["mapping_id"],
            ["playlist_metadata_mappings.id"],
            name=op.f(
                "fk_playlist_mapping_members_mapping_id_playlist_metadata_mappings"
            ),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["track_id"],
            ["tracks.id"],
            name=op.f("fk_playlist_mapping_members_track_id_tracks"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_playlist_mapping_members")),
        sa.UniqueConstraint(
            "mapping_id", "track_id", name="uq_playlist_mapping_members_pair"
        ),
    )
    op.create_index(
        "ix_playlist_mapping_members_mapping_id",
        "playlist_mapping_members",
        ["mapping_id"],
    )

    # -- RLS policies --------------------------------------------------------
    for table in _TABLES:
        op.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
        op.execute(
            sa.text(
                f"CREATE POLICY user_isolation ON {table} FOR ALL "
                f"USING (user_id = current_setting('app.user_id', TRUE))"
            )
        )


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.execute(sa.text(f"DROP POLICY IF EXISTS user_isolation ON {table}"))
        op.execute(sa.text(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY"))

    op.drop_index(
        "ix_playlist_mapping_members_mapping_id",
        table_name="playlist_mapping_members",
    )
    op.drop_table("playlist_mapping_members")

    op.drop_index(
        "ix_playlist_metadata_mappings_user_id",
        table_name="playlist_metadata_mappings",
    )
    op.drop_table("playlist_metadata_mappings")
