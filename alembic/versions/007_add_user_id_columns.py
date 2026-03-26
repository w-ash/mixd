"""Add user_id columns to user-scoped tables + enable Row-Level Security

Adds user_id VARCHAR NOT NULL DEFAULT 'default' to 10 tables, updates unique
constraints to include user_id, and creates RLS policies as defense-in-depth
for multi-user data isolation (v0.6.0).

Revision ID: 007_add_user_id_columns
Revises: f80e19f95cdd
Create Date: 2026-03-24

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "007_add_user_id_columns"
down_revision: str | None = "006_rename_service"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Tables receiving user_id columns
USER_SCOPED_TABLES = [
    "tracks",
    "track_mappings",
    "match_reviews",
    "track_likes",
    "track_plays",
    "connector_plays",
    "playlists",
    "workflows",
    "oauth_tokens",
    "user_settings",
]

# All tables getting RLS policies (includes sync_checkpoints which already has user_id)
RLS_TABLES = [*USER_SCOPED_TABLES, "sync_checkpoints"]

# Old constraints to drop (name -> table for downgrade recreation)
OLD_CONSTRAINTS = {
    "uq_tracks_spotify_id": ("tracks", ["spotify_id"]),
    "uq_tracks_isrc": ("tracks", ["isrc"]),
    "uq_tracks_mbid": ("tracks", ["mbid"]),
    "uq_connector_track_canonical_mapping": (
        "track_mappings",
        ["connector_track_id", "connector_name"],
    ),
    "uq_match_reviews_track_connector": (
        "match_reviews",
        ["track_id", "connector_name", "connector_track_id"],
    ),
    "uq_track_likes_track_likes_track_id": ("track_likes", ["track_id", "service"]),
    "uq_track_plays_deduplication": (
        "track_plays",
        ["track_id", "service", "played_at", "ms_played"],
    ),
    "uq_connector_plays_deduplication": (
        "connector_plays",
        ["connector_name", "connector_track_identifier", "played_at", "ms_played"],
    ),
    "uq_oauth_tokens_service": ("oauth_tokens", ["service"]),
    "uq_user_settings_key": ("user_settings", ["key"]),
}


def upgrade() -> None:
    """Add user_id columns, update constraints, enable RLS."""
    # Step 1: Add user_id columns with server_default for backfill
    for table in USER_SCOPED_TABLES:
        op.add_column(
            table,
            sa.Column("user_id", sa.String(), nullable=False, server_default="default"),
        )

    # Step 2: Drop old unique constraints
    for constraint_name, (table, _columns) in OLD_CONSTRAINTS.items():
        op.drop_constraint(constraint_name, table, type_="unique")

    # Also drop the partial unique index on track_mappings
    op.drop_index("uq_primary_mapping", table_name="track_mappings")

    # Step 3: Create new unique constraints with user_id
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

    # Recreate partial unique index on track_mappings with user_id
    op.create_index(
        "uq_primary_mapping",
        "track_mappings",
        ["user_id", "track_id", "connector_name"],
        unique=True,
        postgresql_where=sa.text("is_primary = TRUE"),
    )

    # Step 4: Add user_id indexes where no composite unique constraint provides coverage
    # (Tables with UNIQUE(user_id, ...) already have an implicit index on user_id as leading column)
    for table in ("playlists", "workflows"):
        op.create_index(f"ix_{table}_user_id", table, ["user_id"])

    # Step 5: Enable Row-Level Security and create policies
    for table in RLS_TABLES:
        op.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
        op.execute(
            sa.text(
                f"CREATE POLICY user_isolation ON {table} "
                f"FOR ALL USING (user_id = current_setting('app.user_id', TRUE))"
            )
        )


def downgrade() -> None:
    """Remove user_id columns, restore old constraints, disable RLS."""
    # Reverse Step 5: Drop RLS policies and disable RLS
    for table in RLS_TABLES:
        op.execute(sa.text(f"DROP POLICY IF EXISTS user_isolation ON {table}"))
        op.execute(sa.text(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY"))

    # Reverse Step 4: Drop user_id indexes (only playlists and workflows have standalone indexes)
    for table in ("playlists", "workflows"):
        op.drop_index(f"ix_{table}_user_id", table_name=table)

    # Reverse Step 3: Drop new constraints
    op.drop_constraint("uq_tracks_user_spotify_id", "tracks", type_="unique")
    op.drop_constraint("uq_tracks_user_isrc", "tracks", type_="unique")
    op.drop_constraint("uq_tracks_user_mbid", "tracks", type_="unique")
    op.drop_constraint(
        "uq_track_mappings_user_connector", "track_mappings", type_="unique"
    )
    op.drop_index("uq_primary_mapping", table_name="track_mappings")
    op.drop_constraint(
        "uq_match_reviews_user_track_connector", "match_reviews", type_="unique"
    )
    op.drop_constraint(
        "uq_track_likes_user_track_service", "track_likes", type_="unique"
    )
    op.drop_constraint("uq_track_plays_deduplication", "track_plays", type_="unique")
    op.drop_constraint(
        "uq_connector_plays_deduplication", "connector_plays", type_="unique"
    )
    op.drop_constraint("uq_oauth_tokens_user_service", "oauth_tokens", type_="unique")
    op.drop_constraint("uq_user_settings_user_key", "user_settings", type_="unique")

    # Reverse Step 2: Restore old constraints
    for constraint_name, (table, columns) in OLD_CONSTRAINTS.items():
        op.create_unique_constraint(constraint_name, table, columns)

    # Restore old partial unique index
    op.create_index(
        "uq_primary_mapping",
        "track_mappings",
        ["track_id", "connector_name"],
        unique=True,
        postgresql_where=sa.text("is_primary = TRUE"),
    )

    # Reverse Step 1: Drop user_id columns
    for table in USER_SCOPED_TABLES:
        op.drop_column(table, "user_id")
