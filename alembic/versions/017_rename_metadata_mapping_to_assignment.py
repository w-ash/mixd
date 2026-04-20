"""Rename playlist metadata mapping → playlist assignment.

v0.7.4 Epic 3 cleanup: "playlist metadata mapping" was found too generic
(it collides with the unrelated v0.4.4 ``playlist_mappings`` PlaylistLink
table and obscures the user-facing meaning). Renames the two tables, the
``mapping_id`` column on the snapshot table, the indexes/constraints, and
the ``MetadataSource`` enum values stored in ``track_preferences`` and
``track_tags`` rows.

ALTER TABLE RENAME preserves RLS policies (Postgres attaches them by OID,
not by name), and the policy ``user_isolation`` doesn't reference the
table name in its USING clause, so the rename is a no-op for RLS.

Revision ID: 017_rename_assignment
Revises: 016_playlist_metadata_mappings
Create Date: 2026-04-19
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "017_rename_assignment"
down_revision: str | None = "016_playlist_metadata_mappings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # -- Rename tables ------------------------------------------------------
    op.rename_table("playlist_metadata_mappings", "playlist_assignments")
    op.rename_table("playlist_mapping_members", "playlist_assignment_members")

    # -- Rename the FK column in the snapshot table ------------------------
    op.alter_column(
        "playlist_assignment_members", "mapping_id", new_column_name="assignment_id"
    )

    # -- Rename indexes / constraints to match the new convention ----------
    op.execute(
        "ALTER INDEX ix_playlist_metadata_mappings_user_id "
        "RENAME TO ix_playlist_assignments_user_id"
    )
    op.execute(
        "ALTER INDEX ix_playlist_mapping_members_mapping_id "
        "RENAME TO ix_playlist_assignment_members_assignment_id"
    )
    op.execute(
        "ALTER TABLE playlist_assignments "
        "RENAME CONSTRAINT uq_playlist_metadata_mappings_action "
        "TO uq_playlist_assignments_action"
    )
    op.execute(
        "ALTER TABLE playlist_assignments "
        "RENAME CONSTRAINT pk_playlist_metadata_mappings "
        "TO pk_playlist_assignments"
    )
    op.execute(
        "ALTER TABLE playlist_assignments "
        "RENAME CONSTRAINT ck_playlist_metadata_mappings_valid_action_type "
        "TO ck_playlist_assignments_valid_action_type"
    )
    op.execute(
        "ALTER TABLE playlist_assignments "
        "RENAME CONSTRAINT "
        "fk_playlist_metadata_mappings_connector_playlist_id_connector_playlists "
        "TO fk_playlist_assignments_connector_playlist_id_connector_playlists"
    )
    op.execute(
        "ALTER TABLE playlist_assignment_members "
        "RENAME CONSTRAINT pk_playlist_mapping_members "
        "TO pk_playlist_assignment_members"
    )
    op.execute(
        "ALTER TABLE playlist_assignment_members "
        "RENAME CONSTRAINT uq_playlist_mapping_members_pair "
        "TO uq_playlist_assignment_members_pair"
    )
    op.execute(
        "ALTER TABLE playlist_assignment_members "
        "RENAME CONSTRAINT "
        "fk_playlist_mapping_members_mapping_id_playlist_metadata_mappings "
        "TO fk_playlist_assignment_members_assignment_id_playlist_assignments"
    )
    op.execute(
        "ALTER TABLE playlist_assignment_members "
        "RENAME CONSTRAINT fk_playlist_mapping_members_track_id_tracks "
        "TO fk_playlist_assignment_members_track_id_tracks"
    )

    # -- Update MetadataSource enum value in existing data -----------------
    op.execute(
        sa.text(
            "UPDATE track_preferences SET source = 'playlist_assignment' "
            "WHERE source = 'playlist_mapping'"
        )
    )
    op.execute(
        sa.text(
            "UPDATE track_tags SET source = 'playlist_assignment' "
            "WHERE source = 'playlist_mapping'"
        )
    )
    op.execute(
        sa.text(
            "UPDATE track_tag_events SET source = 'playlist_assignment' "
            "WHERE source = 'playlist_mapping'"
        )
    )
    op.execute(
        sa.text(
            "UPDATE track_preference_events SET source = 'playlist_assignment' "
            "WHERE source = 'playlist_mapping'"
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE track_preference_events SET source = 'playlist_mapping' "
            "WHERE source = 'playlist_assignment'"
        )
    )
    op.execute(
        sa.text(
            "UPDATE track_tag_events SET source = 'playlist_mapping' "
            "WHERE source = 'playlist_assignment'"
        )
    )
    op.execute(
        sa.text(
            "UPDATE track_tags SET source = 'playlist_mapping' "
            "WHERE source = 'playlist_assignment'"
        )
    )
    op.execute(
        sa.text(
            "UPDATE track_preferences SET source = 'playlist_mapping' "
            "WHERE source = 'playlist_assignment'"
        )
    )

    op.execute(
        "ALTER TABLE playlist_assignment_members "
        "RENAME CONSTRAINT fk_playlist_assignment_members_track_id_tracks "
        "TO fk_playlist_mapping_members_track_id_tracks"
    )
    op.execute(
        "ALTER TABLE playlist_assignment_members "
        "RENAME CONSTRAINT "
        "fk_playlist_assignment_members_assignment_id_playlist_assignments "
        "TO fk_playlist_mapping_members_mapping_id_playlist_metadata_mappings"
    )
    op.execute(
        "ALTER TABLE playlist_assignment_members "
        "RENAME CONSTRAINT uq_playlist_assignment_members_pair "
        "TO uq_playlist_mapping_members_pair"
    )
    op.execute(
        "ALTER TABLE playlist_assignment_members "
        "RENAME CONSTRAINT pk_playlist_assignment_members "
        "TO pk_playlist_mapping_members"
    )
    op.execute(
        "ALTER TABLE playlist_assignments "
        "RENAME CONSTRAINT "
        "fk_playlist_assignments_connector_playlist_id_connector_playlists "
        "TO fk_playlist_metadata_mappings_connector_playlist_id_connector_playlists"
    )
    op.execute(
        "ALTER TABLE playlist_assignments "
        "RENAME CONSTRAINT ck_playlist_assignments_valid_action_type "
        "TO ck_playlist_metadata_mappings_valid_action_type"
    )
    op.execute(
        "ALTER TABLE playlist_assignments "
        "RENAME CONSTRAINT pk_playlist_assignments "
        "TO pk_playlist_metadata_mappings"
    )
    op.execute(
        "ALTER TABLE playlist_assignments "
        "RENAME CONSTRAINT uq_playlist_assignments_action "
        "TO uq_playlist_metadata_mappings_action"
    )
    op.execute(
        "ALTER INDEX ix_playlist_assignment_members_assignment_id "
        "RENAME TO ix_playlist_mapping_members_mapping_id"
    )
    op.execute(
        "ALTER INDEX ix_playlist_assignments_user_id "
        "RENAME TO ix_playlist_metadata_mappings_user_id"
    )

    op.alter_column(
        "playlist_assignment_members", "assignment_id", new_column_name="mapping_id"
    )

    op.rename_table("playlist_assignment_members", "playlist_mapping_members")
    op.rename_table("playlist_assignments", "playlist_metadata_mappings")
