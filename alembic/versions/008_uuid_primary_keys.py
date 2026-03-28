"""Migrate all primary keys and foreign keys from sequential INTEGER to UUID.

Converts 19 tables from auto-increment integer PKs to application-generated UUIDv7.
Existing rows are backfilled with gen_random_uuid() (UUIDv4). New rows use
Python-generated UUIDv7 via the domain entity factories.

Strategy:
  Phase 1: Add new UUID columns alongside old integer columns
  Phase 2: Populate FK UUID columns via join to parent
  Phase 3: Drop old constraints (FKs, PKs, unique, indexes)
  Phase 4: Drop old columns, rename new ones
  Phase 5: Re-create constraints

Revision ID: 008_uuid_primary_keys
Revises: 007_add_user_id_columns
Create Date: 2026-03-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "008_uuid_primary_keys"
down_revision: str | None = "007_add_user_id_columns"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# All tables in the schema
ALL_TABLES = [
    "tracks",
    "connector_tracks",
    "track_mappings",
    "match_reviews",
    "track_metrics",
    "track_likes",
    "track_plays",
    "connector_plays",
    "playlists",
    "connector_playlists",
    "playlist_tracks",
    "playlist_mappings",
    "workflows",
    "workflow_versions",
    "workflow_runs",
    "workflow_run_nodes",
    "oauth_tokens",
    "user_settings",
    "sync_checkpoints",
]

# Foreign key relationships: (child_table, fk_column, parent_table)
FK_RELATIONSHIPS = [
    ("track_mappings", "track_id", "tracks"),
    ("track_mappings", "connector_track_id", "connector_tracks"),
    ("match_reviews", "track_id", "tracks"),
    ("match_reviews", "connector_track_id", "connector_tracks"),
    ("track_metrics", "track_id", "tracks"),
    ("track_likes", "track_id", "tracks"),
    ("track_plays", "track_id", "tracks"),
    ("connector_plays", "resolved_track_id", "tracks"),
    ("playlist_tracks", "playlist_id", "playlists"),
    ("playlist_tracks", "track_id", "tracks"),
    ("playlist_mappings", "playlist_id", "playlists"),
    ("playlist_mappings", "connector_playlist_id", "connector_playlists"),
    ("workflow_versions", "workflow_id", "workflows"),
    ("workflow_runs", "workflow_id", "workflows"),
    ("workflow_run_nodes", "run_id", "workflow_runs"),
]

# Named constraints to drop (collected from db_models.py and migration 007)
NAMED_CONSTRAINTS_TO_DROP = {
    # From migration 007 (user-scoped unique constraints)
    "uq_tracks_user_spotify_id": "tracks",
    "uq_tracks_user_isrc": "tracks",
    "uq_tracks_user_mbid": "tracks",
    "uq_track_mappings_user_connector": "track_mappings",
    "uq_match_reviews_user_track_connector": "match_reviews",
    "uq_track_likes_user_track_service": "track_likes",  # name from migration 007
    "uq_track_plays_deduplication": "track_plays",
    "uq_connector_plays_deduplication": "connector_plays",
    "uq_oauth_tokens_user_service": "oauth_tokens",
    "uq_user_settings_user_key": "user_settings",
    # Original constraints (not user-scoped) — unnamed in migration 001,
    # so SQLAlchemy's naming convention doubled the table name
    "uq_connector_tracks_connector_tracks_connector_name": "connector_tracks",
    "uq_track_metrics_track_metrics_track_id": "track_metrics",
    "uq_playlist_connector": "playlist_mappings",
    "uq_connector_playlist": "playlist_mappings",
    "uq_connector_playlists_connector_playlists_connector_name": "connector_playlists",
    "uq_sync_checkpoints_sync_checkpoints_user_id": "sync_checkpoints",
    "uq_workflows_source_template": "workflows",
    "uq_workflow_versions_workflow_version": "workflow_versions",
}

# Named indexes to drop
NAMED_INDEXES_TO_DROP = {
    "uq_primary_mapping": "track_mappings",
    "ix_track_mappings_track_lookup": "track_mappings",
    "ix_track_mappings_connector_lookup": "track_mappings",
    "ix_match_reviews_track_id": "match_reviews",
    "ix_track_plays_track_id": "track_plays",
    "ix_track_plays_track_played": "track_plays",
    "ix_track_plays_track_service": "track_plays",
    "ix_connector_plays_resolved_track": "connector_plays",
    "ix_connector_plays_unresolved": "connector_plays",
    "ix_workflow_versions_workflow_id": "workflow_versions",
    "ix_workflow_runs_workflow_id_started_at": "workflow_runs",
    "ix_workflow_run_nodes_run_id": "workflow_run_nodes",
}


def upgrade() -> None:
    """Convert all integer PKs and FKs to UUID."""

    # Phase 1: Add new UUID columns alongside old integer columns
    for table in ALL_TABLES:
        op.add_column(
            table,
            sa.Column(
                "uuid_id",
                UUID(as_uuid=True),
                nullable=False,
                server_default=sa.text("gen_random_uuid()"),
            ),
        )

    for child_table, fk_col, _parent_table in FK_RELATIONSHIPS:
        nullable = fk_col == "resolved_track_id"  # Only this FK is nullable
        op.add_column(
            child_table,
            sa.Column(
                f"uuid_{fk_col}",
                UUID(as_uuid=True),
                nullable=nullable if not nullable else True,
            ),
        )

    # Phase 2: Populate FK UUID columns via join to parent
    # Table/column names come from hardcoded constants above — not user input.
    for child_table, fk_col, parent_table in FK_RELATIONSHIPS:
        sql = (
            f"UPDATE {child_table} c SET uuid_{fk_col} = p.uuid_id "  # noqa: S608
            f"FROM {parent_table} p WHERE c.{fk_col} = p.id"
        )
        if fk_col == "resolved_track_id":
            sql += f" AND c.{fk_col} IS NOT NULL"
        op.execute(sa.text(sql))

    # Phase 3: Drop old constraints

    # Drop all FK constraints (use naming convention pattern)
    for child_table, fk_col, parent_table in FK_RELATIONSHIPS:
        fk_name = f"fk_{child_table}_{fk_col}_{parent_table}"
        op.drop_constraint(fk_name, child_table, type_="foreignkey")

    # Drop named unique constraints
    for constraint_name, table in NAMED_CONSTRAINTS_TO_DROP.items():
        op.drop_constraint(constraint_name, table, type_="unique")

    # Drop named indexes
    for index_name, table in NAMED_INDEXES_TO_DROP.items():
        op.drop_index(index_name, table_name=table)

    # Drop primary keys
    for table in ALL_TABLES:
        op.drop_constraint(f"pk_{table}", table, type_="primary")

    # Phase 4: Drop old columns, rename new ones

    # Drop old PK columns and rename uuid_id -> id
    for table in ALL_TABLES:
        op.drop_column(table, "id")
        op.alter_column(table, "uuid_id", new_column_name="id")

    # Drop old FK columns and rename uuid_{fk_col} -> {fk_col}
    for child_table, fk_col, _parent_table in FK_RELATIONSHIPS:
        op.drop_column(child_table, fk_col)
        op.alter_column(child_table, f"uuid_{fk_col}", new_column_name=fk_col)

    # Set NOT NULL on non-nullable FK columns
    for child_table, fk_col, _parent_table in FK_RELATIONSHIPS:
        if fk_col != "resolved_track_id":
            op.alter_column(child_table, fk_col, nullable=False)

    # Remove server_default from id columns (application generates UUIDs)
    for table in ALL_TABLES:
        op.alter_column(table, "id", server_default=None)

    # Phase 5: Re-create constraints

    # Primary keys
    for table in ALL_TABLES:
        op.create_primary_key(f"pk_{table}", table, ["id"])

    # Foreign keys
    for child_table, fk_col, parent_table in FK_RELATIONSHIPS:
        fk_name = f"fk_{child_table}_{fk_col}_{parent_table}"
        op.create_foreign_key(
            fk_name, child_table, parent_table, [fk_col], ["id"], ondelete="CASCADE"
        )

    # User-scoped unique constraints (from migration 007)
    op.create_unique_constraint(
        "uq_tracks_user_spotify_id", "tracks", ["user_id", "spotify_id"]
    )
    op.create_unique_constraint("uq_tracks_user_isrc", "tracks", ["user_id", "isrc"])
    op.create_unique_constraint("uq_tracks_user_mbid", "tracks", ["user_id", "mbid"])
    op.create_unique_constraint(
        "uq_track_mappings_user_connector",
        "track_mappings",
        ["user_id", "connector_track_id", "connector_name"],
    )
    op.create_unique_constraint(
        "uq_match_reviews_user_track_connector",
        "match_reviews",
        ["user_id", "track_id", "connector_name", "connector_track_id"],
    )
    op.create_unique_constraint(
        "uq_track_likes_user_track_service",
        "track_likes",
        ["user_id", "track_id", "service"],
    )
    op.create_unique_constraint(
        "uq_track_plays_deduplication",
        "track_plays",
        ["user_id", "track_id", "service", "played_at", "ms_played"],
    )
    op.create_unique_constraint(
        "uq_connector_plays_deduplication",
        "connector_plays",
        [
            "user_id",
            "connector_name",
            "connector_track_identifier",
            "played_at",
            "ms_played",
        ],
    )
    op.create_unique_constraint(
        "uq_oauth_tokens_user_service", "oauth_tokens", ["user_id", "service"]
    )
    op.create_unique_constraint(
        "uq_user_settings_user_key", "user_settings", ["user_id", "key"]
    )

    # Non-user-scoped unique constraints
    op.create_unique_constraint(
        "uq_connector_tracks_connector_name",
        "connector_tracks",
        ["connector_name", "connector_track_identifier"],
    )
    op.create_unique_constraint(
        "uq_track_metrics_track_id",
        "track_metrics",
        ["track_id", "connector_name", "metric_type"],
    )
    op.create_unique_constraint(
        "uq_playlist_connector", "playlist_mappings", ["playlist_id", "connector_name"]
    )
    op.create_unique_constraint(
        "uq_connector_playlist", "playlist_mappings", ["connector_playlist_id"]
    )
    op.create_unique_constraint(
        "uq_connector_playlists_connector_name",
        "connector_playlists",
        ["connector_name", "connector_playlist_identifier"],
    )
    op.create_unique_constraint(
        "uq_sync_checkpoints_user_id",
        "sync_checkpoints",
        ["user_id", "service", "entity_type"],
    )
    op.create_unique_constraint(
        "uq_workflows_source_template", "workflows", ["source_template"]
    )
    op.create_unique_constraint(
        "uq_workflow_versions_workflow_version",
        "workflow_versions",
        ["workflow_id", "version"],
    )

    # Partial unique index for primary mappings
    op.create_index(
        "uq_primary_mapping",
        "track_mappings",
        ["user_id", "track_id", "connector_name"],
        unique=True,
        postgresql_where=sa.text("is_primary = TRUE"),
    )

    # Performance indexes involving FK columns
    op.create_index("ix_track_mappings_track_lookup", "track_mappings", ["track_id"])
    op.create_index(
        "ix_track_mappings_connector_lookup", "track_mappings", ["connector_track_id"]
    )
    op.create_index("ix_match_reviews_track_id", "match_reviews", ["track_id"])
    op.create_index("ix_track_plays_track_id", "track_plays", ["track_id"])
    op.create_index(
        "ix_track_plays_track_played", "track_plays", ["track_id", "played_at"]
    )
    op.create_index(
        "ix_track_plays_track_service", "track_plays", ["track_id", "service"]
    )
    op.create_index(
        "ix_connector_plays_resolved_track", "connector_plays", ["resolved_track_id"]
    )
    op.create_index(
        "ix_connector_plays_unresolved",
        "connector_plays",
        ["connector_name", "resolved_track_id"],
    )
    op.create_index(
        "ix_workflow_versions_workflow_id", "workflow_versions", ["workflow_id"]
    )
    op.create_index(
        "ix_workflow_runs_workflow_id_started_at",
        "workflow_runs",
        ["workflow_id", "started_at"],
    )
    op.create_index("ix_workflow_run_nodes_run_id", "workflow_run_nodes", ["run_id"])


def downgrade() -> None:
    """Revert UUID PKs back to integer PKs.

    WARNING: This is a lossy downgrade — UUID values cannot be converted back
    to the original integer sequences. All integer IDs will be newly generated.
    """
    raise NotImplementedError(
        "Downgrade from UUID to integer PKs is not supported. "
        "Restore from a database backup if needed."
    )
