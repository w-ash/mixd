"""PostgreSQL optimization: JSONB, ARRAY, pg_trgm, BRIN, artists_text, VARCHAR cleanup.

Migrates SQLite-era patterns to PostgreSQL-native:
- JSON → JSONB for all JSON columns (25% smaller, GIN-indexable)
- source_services JSON → ARRAY(VARCHAR) for native array operations
- Add artists_text denormalized column for search/sort
- Enable pg_trgm extension + GIN trigram indexes for ILIKE acceleration
- Add BRIN index on track_plays.played_at for time-series queries
- Add status indexes on workflow_runs, playlist_mappings
- Remove unnecessary VARCHAR(N) length constraints

Revision ID: 002_pg_opt
Revises: 001_initial
Create Date: 2026-03-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "002_pg_opt"
down_revision: str = "001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── Extensions ──────────────────────────────────────────────────
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # ── Phase 2A: JSON → JSONB ──────────────────────────────────────
    # PostgreSQL handles JSON→JSONB cast automatically via ALTER COLUMN TYPE
    _convert_json_to_jsonb("tracks", ["artists"])
    _convert_json_to_jsonb("connector_tracks", ["artists", "raw_metadata"])
    _convert_json_to_jsonb("track_mappings", ["confidence_evidence"])
    _convert_json_to_jsonb("match_reviews", ["confidence_evidence"])
    _convert_json_to_jsonb("track_plays", ["context"])
    _convert_json_to_jsonb("connector_plays", ["raw_metadata"])
    _convert_json_to_jsonb("connector_playlists", ["items", "raw_metadata"])
    _convert_json_to_jsonb("workflows", ["definition"])
    _convert_json_to_jsonb("workflow_versions", ["definition"])
    _convert_json_to_jsonb("workflow_runs", ["definition_snapshot", "output_tracks"])
    _convert_json_to_jsonb("workflow_run_nodes", ["node_details"])

    # ── Phase 2C: source_services JSON → ARRAY(VARCHAR) ─────────────
    # First convert existing JSON arrays to text[], then alter column type
    # Use a raw SQL approach: create temp column, migrate data, swap
    op.add_column(
        "track_plays",
        sa.Column("source_services_new", ARRAY(sa.String()), nullable=True),
    )
    # Migrate existing JSON array data to native ARRAY
    op.execute("""
        UPDATE track_plays
        SET source_services_new = (
            SELECT array_agg(elem::text)
            FROM jsonb_array_elements_text(source_services::jsonb) AS elem
        )
        WHERE source_services IS NOT NULL
    """)
    op.drop_column("track_plays", "source_services")
    op.alter_column(
        "track_plays",
        "source_services_new",
        new_column_name="source_services",
    )

    # ── Phase 3A: artists_text denormalized column ──────────────────
    op.add_column(
        "tracks",
        sa.Column("artists_text", sa.String(), nullable=True),
    )
    # Populate from JSONB artists.names array
    op.execute("""
        UPDATE tracks
        SET artists_text = (
            SELECT string_agg(name, ', ')
            FROM jsonb_array_elements_text(artists->'names') AS name
        )
        WHERE artists IS NOT NULL
    """)

    # ── Phase 4C: Remove unnecessary VARCHAR(N) constraints ─────────
    # PostgreSQL TEXT and VARCHAR have identical performance; length limits
    # only add validation overhead. Keep String(32) for connector_name
    # (documentation value) and short enum-like fields.
    _widen_varchar("tracks", "title")
    _widen_varchar("tracks", "album")
    _widen_varchar("tracks", "title_normalized")
    _widen_varchar("tracks", "artist_normalized")
    _widen_varchar("tracks", "title_stripped")
    _widen_varchar("tracks", "spotify_id")
    _widen_varchar("connector_tracks", "connector_track_identifier")
    _widen_varchar("connector_tracks", "title")
    _widen_varchar("connector_tracks", "album")
    _widen_varchar("connector_playlists", "name", current_type=sa.String())
    _widen_varchar("playlists", "name")
    _widen_varchar("workflows", "name")
    _widen_varchar("track_plays", "import_batch_id")
    _widen_varchar("connector_plays", "connector_track_identifier")
    _widen_varchar("connector_plays", "import_batch_id")
    _widen_varchar("sync_checkpoints", "user_id")

    # ── Phase 3A: GIN trigram indexes for ILIKE acceleration ────────
    op.create_index(
        "ix_tracks_title_trgm", "tracks", ["title"],
        postgresql_using="gin",
        postgresql_ops={"title": "gin_trgm_ops"},
    )
    op.create_index(
        "ix_tracks_album_trgm", "tracks", ["album"],
        postgresql_using="gin",
        postgresql_ops={"album": "gin_trgm_ops"},
    )
    op.create_index(
        "ix_tracks_artists_text_trgm", "tracks", ["artists_text"],
        postgresql_using="gin",
        postgresql_ops={"artists_text": "gin_trgm_ops"},
    )

    # ── Phase 2B: GIN index on artists JSONB ────────────────────────
    op.create_index(
        "ix_tracks_artists_gin", "tracks", ["artists"],
        postgresql_using="gin",
        postgresql_ops={"artists": "jsonb_path_ops"},
    )

    # ── Phase 4A: Status/state column indexes ───────────────────────
    # match_reviews.status already indexed in initial migration
    op.create_index("ix_workflow_runs_status", "workflow_runs", ["status"])
    op.create_index(
        "ix_playlist_mappings_sync_status", "playlist_mappings", ["sync_status"]
    )

    # ── Phase 4B: BRIN index on track_plays.played_at ───────────────
    op.create_index(
        "ix_track_plays_played_at_brin", "track_plays", ["played_at"],
        postgresql_using="brin",
    )


def downgrade() -> None:
    # Drop new indexes
    op.drop_index("ix_track_plays_played_at_brin", table_name="track_plays")
    op.drop_index("ix_playlist_mappings_sync_status", table_name="playlist_mappings")
    op.drop_index("ix_workflow_runs_status", table_name="workflow_runs")
    op.drop_index("ix_tracks_artists_gin", table_name="tracks")
    op.drop_index("ix_tracks_artists_text_trgm", table_name="tracks")
    op.drop_index("ix_tracks_album_trgm", table_name="tracks")
    op.drop_index("ix_tracks_title_trgm", table_name="tracks")

    # Drop artists_text column
    op.drop_column("tracks", "artists_text")

    # Revert source_services ARRAY → JSON
    op.add_column(
        "track_plays",
        sa.Column("source_services_old", sa.JSON(), nullable=True),
    )
    op.execute("""
        UPDATE track_plays
        SET source_services_old = to_jsonb(source_services)
        WHERE source_services IS NOT NULL
    """)
    op.drop_column("track_plays", "source_services")
    op.alter_column(
        "track_plays",
        "source_services_old",
        new_column_name="source_services",
    )

    # Revert JSONB → JSON (reverse order of upgrade)
    _convert_jsonb_to_json("workflow_run_nodes", ["node_details"])
    _convert_jsonb_to_json("workflow_runs", ["definition_snapshot", "output_tracks"])
    _convert_jsonb_to_json("workflow_versions", ["definition"])
    _convert_jsonb_to_json("workflows", ["definition"])
    _convert_jsonb_to_json("connector_playlists", ["items", "raw_metadata"])
    _convert_jsonb_to_json("connector_plays", ["raw_metadata"])
    _convert_jsonb_to_json("track_plays", ["context"])
    _convert_jsonb_to_json("match_reviews", ["confidence_evidence"])
    _convert_jsonb_to_json("track_mappings", ["confidence_evidence"])
    _convert_jsonb_to_json("connector_tracks", ["artists", "raw_metadata"])
    _convert_jsonb_to_json("tracks", ["artists"])

    op.execute("DROP EXTENSION IF EXISTS pg_trgm")


# ── Helpers ─────────────────────────────────────────────────────────

def _convert_json_to_jsonb(table: str, columns: list[str]) -> None:
    """Convert JSON columns to JSONB (PostgreSQL auto-casts)."""
    for col in columns:
        op.alter_column(
            table, col,
            type_=JSONB,
            existing_type=sa.JSON(),
            postgresql_using=f"{col}::jsonb",
        )


def _convert_jsonb_to_json(table: str, columns: list[str]) -> None:
    """Revert JSONB columns back to JSON."""
    for col in columns:
        op.alter_column(
            table, col,
            type_=sa.JSON(),
            existing_type=JSONB,
            postgresql_using=f"{col}::json",
        )


def _widen_varchar(
    table: str,
    column: str,
    current_type: sa.types.TypeEngine | None = None,
) -> None:
    """Remove VARCHAR(N) length constraint → VARCHAR (unbounded)."""
    op.alter_column(
        table, column,
        type_=sa.String(),
        existing_type=current_type or sa.String(255),
    )
