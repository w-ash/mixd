"""Initial PostgreSQL schema.

Fresh migration generated from db_models.py for PostgreSQL.
Replaces the SQLite migration chain (archived in versions_sqlite_archive/).

Revision ID: 001_initial
Revises:
Create Date: 2026-03-17
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- tracks ---
    op.create_table(
        "tracks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("artists", sa.JSON(), nullable=False),
        sa.Column("album", sa.String(255), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("release_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("isrc", sa.String(32), nullable=True),
        sa.Column("spotify_id", sa.String(64), nullable=True),
        sa.Column("mbid", sa.String(36), nullable=True),
        sa.Column("title_normalized", sa.String(255), nullable=True),
        sa.Column("artist_normalized", sa.String(255), nullable=True),
        sa.Column("title_stripped", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("spotify_id", name="uq_tracks_spotify_id"),
        sa.UniqueConstraint("isrc", name="uq_tracks_isrc"),
        sa.UniqueConstraint("mbid", name="uq_tracks_mbid"),
    )
    op.create_index("ix_tracks_isrc", "tracks", ["isrc"])
    op.create_index("ix_tracks_spotify_id", "tracks", ["spotify_id"])
    op.create_index("ix_tracks_mbid", "tracks", ["mbid"])
    op.create_index("ix_tracks_title", "tracks", ["title"])
    op.create_index("ix_tracks_normalized_lookup", "tracks", ["title_normalized", "artist_normalized"])
    op.create_index("ix_tracks_stripped_lookup", "tracks", ["title_stripped", "artist_normalized"])

    # --- connector_tracks ---
    op.create_table(
        "connector_tracks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("connector_name", sa.String(32), nullable=False),
        sa.Column("connector_track_identifier", sa.String(64), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("artists", sa.JSON(), nullable=False),
        sa.Column("album", sa.String(255), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("isrc", sa.String(32), nullable=True),
        sa.Column("release_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw_metadata", sa.JSON(), nullable=False),
        sa.Column("last_updated", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("connector_name", "connector_track_identifier"),
    )
    op.create_index(None, "connector_tracks", ["connector_name", "isrc"])
    op.create_index("ix_connector_tracks_isrc", "connector_tracks", ["isrc"])

    # --- track_mappings ---
    op.create_table(
        "track_mappings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("track_id", sa.Integer(), sa.ForeignKey("tracks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("connector_track_id", sa.Integer(), sa.ForeignKey("connector_tracks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("connector_name", sa.String(32), nullable=False),
        sa.Column("match_method", sa.String(32), nullable=False),
        sa.Column("confidence", sa.Integer(), nullable=False),
        sa.Column("confidence_evidence", sa.JSON(), nullable=True),
        sa.Column("origin", sa.String(20), nullable=False, server_default="automatic"),
        sa.Column("is_primary", sa.Boolean(), nullable=False, default=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("connector_track_id", "connector_name", name="uq_connector_track_canonical_mapping"),
    )
    op.create_index(
        "uq_primary_mapping",
        "track_mappings",
        ["track_id", "connector_name"],
        unique=True,
        postgresql_where=sa.text("is_primary = TRUE"),
    )
    op.create_index("ix_track_mappings_track_lookup", "track_mappings", ["track_id"])
    op.create_index("ix_track_mappings_connector_lookup", "track_mappings", ["connector_track_id"])
    op.create_index("ix_track_mappings_connector_name", "track_mappings", ["connector_name"])

    # --- match_reviews ---
    op.create_table(
        "match_reviews",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("track_id", sa.Integer(), sa.ForeignKey("tracks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("connector_name", sa.String(32), nullable=False),
        sa.Column("connector_track_id", sa.Integer(), sa.ForeignKey("connector_tracks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("match_method", sa.String(32), nullable=False),
        sa.Column("confidence", sa.Integer(), nullable=False),
        sa.Column("match_weight", sa.Float(), nullable=False),
        sa.Column("confidence_evidence", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("track_id", "connector_name", "connector_track_id", name="uq_match_reviews_track_connector"),
    )
    op.create_index("ix_match_reviews_status", "match_reviews", ["status"])
    op.create_index("ix_match_reviews_track_id", "match_reviews", ["track_id"])

    # --- track_metrics ---
    op.create_table(
        "track_metrics",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("track_id", sa.Integer(), sa.ForeignKey("tracks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("connector_name", sa.String(32), nullable=False),
        sa.Column("metric_type", sa.String(32), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("track_id", "connector_name", "metric_type"),
    )
    op.create_index(None, "track_metrics", ["track_id", "connector_name", "metric_type"])

    # --- track_likes ---
    op.create_table(
        "track_likes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("track_id", sa.Integer(), sa.ForeignKey("tracks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("service", sa.String(32), nullable=False),
        sa.Column("is_liked", sa.Boolean(), nullable=False, default=True),
        sa.Column("liked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_synced", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("track_id", "service"),
    )
    op.create_index(None, "track_likes", ["service", "is_liked"])

    # --- track_plays ---
    op.create_table(
        "track_plays",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("track_id", sa.Integer(), sa.ForeignKey("tracks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("service", sa.String(32), nullable=False),
        sa.Column("played_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ms_played", sa.Integer(), nullable=True),
        sa.Column("context", sa.JSON(), nullable=True),
        sa.Column("source_services", sa.JSON(), nullable=True),
        sa.Column("import_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("import_source", sa.String(32), nullable=True),
        sa.Column("import_batch_id", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("track_id", "service", "played_at", "ms_played", name="uq_track_plays_deduplication"),
    )
    op.create_index("ix_track_plays_service", "track_plays", ["service"])
    op.create_index("ix_track_plays_played_at", "track_plays", ["played_at"])
    op.create_index("ix_track_plays_import_source", "track_plays", ["import_source"])
    op.create_index("ix_track_plays_import_batch", "track_plays", ["import_batch_id"])
    op.create_index("ix_track_plays_track_id", "track_plays", ["track_id"])
    op.create_index("ix_track_plays_track_played", "track_plays", ["track_id", "played_at"])
    op.create_index("ix_track_plays_track_service", "track_plays", ["track_id", "service"])

    # --- connector_plays ---
    op.create_table(
        "connector_plays",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("connector_name", sa.String(32), nullable=False),
        sa.Column("connector_track_identifier", sa.String(255), nullable=False),
        sa.Column("played_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ms_played", sa.Integer(), nullable=True),
        sa.Column("raw_metadata", sa.JSON(), nullable=False),
        sa.Column("resolved_track_id", sa.Integer(), sa.ForeignKey("tracks.id", ondelete="CASCADE"), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("import_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("import_source", sa.String(32), nullable=True),
        sa.Column("import_batch_id", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("connector_name", "connector_track_identifier", "played_at", "ms_played", name="uq_connector_plays_deduplication"),
    )
    op.create_index("ix_connector_plays_connector", "connector_plays", ["connector_name"])
    op.create_index("ix_connector_plays_played_at", "connector_plays", ["played_at"])
    op.create_index("ix_connector_plays_resolved_track", "connector_plays", ["resolved_track_id"])
    op.create_index("ix_connector_plays_unresolved", "connector_plays", ["connector_name", "resolved_track_id"])
    op.create_index("ix_connector_plays_import_batch", "connector_plays", ["import_batch_id"])

    # --- playlists ---
    op.create_table(
        "playlists",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.String(1000), nullable=True),
        sa.Column("track_count", sa.Integer(), nullable=False, default=0),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    # --- connector_playlists ---
    op.create_table(
        "connector_playlists",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("connector_name", sa.String(), nullable=False),
        sa.Column("connector_playlist_identifier", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("owner", sa.String(), nullable=True),
        sa.Column("owner_id", sa.String(), nullable=True),
        sa.Column("is_public", sa.Boolean(), nullable=False),
        sa.Column("collaborative", sa.Boolean(), nullable=False, default=False),
        sa.Column("follower_count", sa.Integer(), nullable=True),
        sa.Column("items", sa.JSON(), nullable=False),
        sa.Column("raw_metadata", sa.JSON(), nullable=False),
        sa.Column("last_updated", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("connector_name", "connector_playlist_identifier"),
    )

    # --- playlist_mappings ---
    op.create_table(
        "playlist_mappings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("playlist_id", sa.Integer(), sa.ForeignKey("playlists.id", ondelete="CASCADE"), nullable=False),
        sa.Column("connector_name", sa.String(32), nullable=False),
        sa.Column("connector_playlist_id", sa.Integer(), sa.ForeignKey("connector_playlists.id", ondelete="CASCADE"), nullable=False),
        sa.Column("last_synced", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sync_direction", sa.String(10), nullable=False, server_default="push"),
        sa.Column("sync_status", sa.String(20), nullable=False, server_default="never_synced"),
        sa.Column("last_sync_error", sa.String(), nullable=True),
        sa.Column("last_sync_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sync_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sync_tracks_added", sa.Integer(), nullable=True),
        sa.Column("last_sync_tracks_removed", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("playlist_id", "connector_name", name="uq_playlist_connector"),
        sa.UniqueConstraint("connector_playlist_id", name="uq_connector_playlist"),
    )

    # --- playlist_tracks ---
    op.create_table(
        "playlist_tracks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("playlist_id", sa.Integer(), sa.ForeignKey("playlists.id", ondelete="CASCADE"), nullable=False),
        sa.Column("track_id", sa.Integer(), sa.ForeignKey("tracks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sort_key", sa.String(32), nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(None, "playlist_tracks", ["playlist_id", "sort_key"])

    # --- workflows ---
    op.create_table(
        "workflows",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.String(1000), nullable=True),
        sa.Column("definition", sa.JSON(), nullable=False),
        sa.Column("is_template", sa.Boolean(), nullable=False, default=False),
        sa.Column("source_template", sa.String(100), nullable=True),
        sa.Column("definition_version", sa.Integer(), nullable=False, default=1),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("source_template", name="uq_workflows_source_template"),
    )
    op.create_index("ix_workflows_is_template", "workflows", ["is_template"])

    # --- workflow_versions ---
    op.create_table(
        "workflow_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workflow_id", sa.Integer(), sa.ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("definition", sa.JSON(), nullable=False),
        sa.Column("change_summary", sa.String(1000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("workflow_id", "version", name="uq_workflow_versions_workflow_version"),
    )
    op.create_index("ix_workflow_versions_workflow_id", "workflow_versions", ["workflow_id"])

    # --- workflow_runs ---
    op.create_table(
        "workflow_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workflow_id", sa.Integer(), sa.ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, default="pending"),
        sa.Column("definition_snapshot", sa.JSON(), nullable=False),
        sa.Column("definition_version", sa.Integer(), nullable=False, default=1),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("output_track_count", sa.Integer(), nullable=True),
        sa.Column("output_playlist_id", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.String(2000), nullable=True),
        sa.Column("output_tracks", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_workflow_runs_workflow_id_started_at", "workflow_runs", ["workflow_id", "started_at"])

    # --- workflow_run_nodes ---
    op.create_table(
        "workflow_run_nodes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("node_id", sa.String(100), nullable=False),
        sa.Column("node_type", sa.String(100), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, default="pending"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False, default=0),
        sa.Column("input_track_count", sa.Integer(), nullable=True),
        sa.Column("output_track_count", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.String(2000), nullable=True),
        sa.Column("execution_order", sa.Integer(), nullable=False, default=0),
        sa.Column("node_details", sa.JSON(), nullable=True),
    )
    op.create_index("ix_workflow_run_nodes_run_id", "workflow_run_nodes", ["run_id"])

    # --- sync_checkpoints ---
    op.create_table(
        "sync_checkpoints",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("service", sa.String(32), nullable=False),
        sa.Column("entity_type", sa.String(32), nullable=False),
        sa.Column("last_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cursor", sa.String(1024), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "service", "entity_type"),
    )


def downgrade() -> None:
    op.drop_table("sync_checkpoints")
    op.drop_table("workflow_run_nodes")
    op.drop_table("workflow_runs")
    op.drop_table("workflow_versions")
    op.drop_table("workflows")
    op.drop_table("playlist_tracks")
    op.drop_table("playlist_mappings")
    op.drop_table("connector_playlists")
    op.drop_table("playlists")
    op.drop_table("connector_plays")
    op.drop_table("track_plays")
    op.drop_table("track_likes")
    op.drop_table("track_metrics")
    op.drop_table("match_reviews")
    op.drop_table("track_mappings")
    op.drop_table("connector_tracks")
    op.drop_table("tracks")
