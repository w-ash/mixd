"""Artists become first-class: canonical rows, connector records, credits, favorites.

Seven tables, mirroring the track stack one level up. ``artists`` is the
per-user canonical (cross-user identity is deferred to v1.1.0, so a global
table would smuggle in merge rights this cycle has not earned);
``connector_artists`` is the global service-record cache with no ``user_id``
and no RLS, exactly like ``connector_tracks``; ``artist_mappings`` joins them
per user; ``artist_favorites`` is a Mixd-only presence row; ``track_artists``
is the credit association object the JSONB expands into (061 fills it);
``connector_track_artists`` is its global twin — what a service says about a
track's line-up, pointing at the service's own artist record, no ``user_id``
and no RLS (061 fills it too); and ``artist_aliases`` holds each service's
alternative names for its own record.

Two rules the constraints encode:

* **External identifiers are references, never keys.** ``artists.mbid`` gets a
  plain index and no unique constraint — a MusicBrainz merge can leave two
  canonical artists on one MBID, and a constraint there would abort the alias
  refresh instead of letting the planner dedupe. ``artists.name`` is
  non-unique for the same reason in reverse: same-name artists are real.
  Identity uniqueness lives in ``uq_artist_mappings_user_connector``.
* **No supersession on ``artist_mappings``.** Artist ids are treated as
  permanent, so a changed decision rewrites the row; the five supersession
  columns that make ``track_mappings`` append-only are deliberately absent and
  can be added later as columns, not code.

The vocabularies are inlined rather than imported from the domain: a migration
is a frozen record of the schema at this revision.
``tests/unit/migrations/test_060_artist_tables.py`` asserts they still equal
the domain sets — growing one means a new migration that recreates the
constraint.

Three indexes are migration-only and absent from ``metadata.create_all``:
``ix_artists_name_trgm`` and ``ix_connector_artists_name_trgm`` need the
pg_trgm extension, and ``ix_connector_artists_raw_metadata_gin`` is a JSONB
GIN. They are listed in ``_MIGRATION_ONLY_INDEXES`` in the schema gate.

Plain DDL, no ``CONCURRENTLY``: the release command runs over the Neon pooler
URL, where ``autocommit_block`` cannot run (see 049 and 051).

Revision ID: 060_artist_tables
Revises: 059_identity_lives_in_mappings
Create Date: 2026-09-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "060_artist_tables"
down_revision: str | None = "059_identity_lives_in_mappings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Sorted, so the rendered DDL matches the ORM's constraint text exactly.
ARTIST_KINDS: tuple[str, ...] = ("group", "other", "person")

MATCH_METHODS: tuple[str, ...] = (
    "artist_title",
    "canonical_reuse",
    "direct",
    "direct_import",
    "direct_import_stale_id",
    "isrc",
    "isrc_match",
    "isrc_match_stale_id",
    "isrc_suspect",
    "lastfm_discovery",
    "lastfm_import",
    "lastfm_import_raw_alias",
    "mbid",
    "mbid_match",
    "search_fallback",
    "search_fallback_stale_id",
    "spotify_connector_play_resolver",
    "spotify_redirect",
)

MAPPING_ORIGINS: tuple[str, ...] = ("automatic", "manual_override")

# Tables carrying a ``user_id`` of their own. ``connector_artists``,
# ``connector_track_artists`` and ``artist_aliases`` are global service facts
# and stay outside RLS, like ``connector_tracks``.
_RLS_TABLES: tuple[str, ...] = (
    "artists",
    "artist_mappings",
    "artist_favorites",
    "track_artists",
)

_MIGRATION_ONLY_INDEXES: tuple[tuple[str, str], ...] = (
    ("ix_artists_name_trgm", "artists"),
    ("ix_connector_artists_name_trgm", "connector_artists"),
    ("ix_connector_artists_raw_metadata_gin", "connector_artists"),
    ("ix_artist_aliases_lower_name", "artist_aliases"),
)


def _in_list(column_name: str, vocabulary: tuple[str, ...]) -> str:
    members = ", ".join(f"'{value}'" for value in vocabulary)
    return f"{column_name} IN ({members})"


def _enable_rls(table: str) -> None:
    op.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
    op.execute(
        sa.text(
            f"CREATE POLICY user_isolation ON {table} FOR ALL "
            f"USING (user_id = current_setting('app.user_id', TRUE))"
        )
    )


def _disable_rls(table: str) -> None:
    op.execute(sa.text(f"DROP POLICY IF EXISTS user_isolation ON {table}"))
    op.execute(sa.text(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY"))


def _create_artists() -> None:
    op.create_table(
        "artists",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        # No server default: an INSERT that omits the tenant must fail with
        # NotNullViolation rather than land in a phantom tenant (055).
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("mbid", sa.String(64), nullable=True),
        sa.Column("kind", sa.String(16), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_artists")),
        sa.CheckConstraint(
            _in_list("kind", ARTIST_KINDS),
            name=op.f("ck_artists_kind_vocabulary"),
        ),
    )
    op.create_index("ix_artists_user_name", "artists", ["user_id", "name"])
    # Non-unique by design: the MBID is a reference, not a key.
    op.create_index("ix_artists_mbid", "artists", ["mbid"])
    op.create_index("ix_artists_user_updated_at", "artists", ["user_id", "updated_at"])
    _enable_rls("artists")


def _create_connector_artists() -> None:
    op.create_table(
        "connector_artists",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("connector_name", sa.String(32), nullable=False),
        sa.Column("connector_artist_identifier", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("raw_metadata", postgresql.JSONB(), nullable=False),
        sa.Column("last_updated", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_connector_artists")),
        sa.UniqueConstraint(
            "connector_name",
            "connector_artist_identifier",
            name="uq_connector_artists_identity",
        ),
    )
    op.create_index("ix_connector_artists_name", "connector_artists", ["name"])


def _create_artist_mappings() -> None:
    op.create_table(
        "artist_mappings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("artist_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("connector_artist_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("connector_name", sa.String(32), nullable=False),
        sa.Column("match_method", sa.String(32), nullable=False),
        sa.Column("confidence", sa.Integer(), nullable=False),
        sa.Column("confidence_evidence", postgresql.JSONB(), nullable=True),
        sa.Column("origin", sa.String(20), server_default="automatic", nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_artist_mappings")),
        # CASCADE: with no supersession these rows are current identity, not
        # history, so a deleted artist strands nothing.
        sa.ForeignKeyConstraint(
            ["artist_id"],
            ["artists.id"],
            name=op.f("fk_artist_mappings_artist_id_artists"),
            ondelete="CASCADE",
        ),
        # RESTRICT: the connector row is a shared cache entry other tenants'
        # mappings may name.
        sa.ForeignKeyConstraint(
            ["connector_artist_id"],
            ["connector_artists.id"],
            name=op.f("fk_artist_mappings_connector_artist_id_connector_artists"),
            ondelete="RESTRICT",
        ),
        # The live key the generic mapping repository conflicts on. A plain
        # unique constraint, not a partial index: every row here is live.
        sa.UniqueConstraint(
            "user_id",
            "connector_artist_id",
            name="uq_artist_mappings_user_connector",
        ),
        sa.CheckConstraint(
            _in_list("match_method", MATCH_METHODS),
            name=op.f("ck_artist_mappings_match_method_vocabulary"),
        ),
        sa.CheckConstraint(
            _in_list("origin", MAPPING_ORIGINS),
            name=op.f("ck_artist_mappings_origin_vocabulary"),
        ),
    )
    op.create_index(
        "uq_artist_mappings_primary",
        "artist_mappings",
        ["user_id", "artist_id", "connector_name"],
        unique=True,
        postgresql_where=sa.text("is_primary = TRUE"),
    )
    op.create_index(
        "ix_artist_mappings_artist_lookup", "artist_mappings", ["user_id", "artist_id"]
    )
    op.create_index(
        "ix_artist_mappings_connector_name", "artist_mappings", ["connector_name"]
    )
    _enable_rls("artist_mappings")


def _create_artist_favorites() -> None:
    op.create_table(
        "artist_favorites",
        # The pair is the key: no surrogate id, because nothing references a
        # favorite and DELETE-on-the-pair is how unfavoriting works.
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("artist_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("favorited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "user_id", "artist_id", name=op.f("pk_artist_favorites")
        ),
        sa.ForeignKeyConstraint(
            ["artist_id"],
            ["artists.id"],
            name=op.f("fk_artist_favorites_artist_id_artists"),
            ondelete="CASCADE",
        ),
    )
    _enable_rls("artist_favorites")


def _create_track_artists() -> None:
    op.create_table(
        "track_artists",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("track_id", postgresql.UUID(as_uuid=True), nullable=False),
        # Nullable and SET NULL: a Various Artists credit never gets an artist
        # row, and an unresolved credit has not been minted one yet. The credit
        # must survive without it.
        sa.Column("artist_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("credited_name", sa.String(), nullable=False),
        sa.Column("join_phrase", sa.String(), nullable=True),
        sa.Column("role", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_track_artists")),
        sa.ForeignKeyConstraint(
            ["track_id"],
            ["tracks.id"],
            name=op.f("fk_track_artists_track_id_tracks"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["artist_id"],
            ["artists.id"],
            name=op.f("fk_track_artists_artist_id_artists"),
            ondelete="SET NULL",
        ),
        # Also the backfill's ON CONFLICT arbiter.
        sa.UniqueConstraint(
            "track_id", "position", name="uq_track_artists_track_position"
        ),
        sa.CheckConstraint(
            "position >= 0", name=op.f("ck_track_artists_position_nonneg")
        ),
    )
    # The SET NULL FK needs its own index for the delete-time probe.
    op.create_index("ix_track_artists_artist", "track_artists", ["artist_id"])
    op.create_index(
        "ix_track_artists_user_artist", "track_artists", ["user_id", "artist_id"]
    )
    _enable_rls("track_artists")


def _create_connector_track_artists() -> None:
    op.create_table(
        "connector_track_artists",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("connector_track_id", postgresql.UUID(as_uuid=True), nullable=False),
        # Nullable and SET NULL: a credit whose service gave no artist id
        # (Apple's song payload) has no record to point at and keeps its name.
        sa.Column("connector_artist_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("credited_name", sa.String(), nullable=False),
        sa.Column("join_phrase", sa.String(), nullable=True),
        sa.Column("role", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_connector_track_artists")),
        sa.ForeignKeyConstraint(
            ["connector_track_id"],
            ["connector_tracks.id"],
            name=op.f("fk_connector_track_artists_connector_track_id_connector_tracks"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["connector_artist_id"],
            ["connector_artists.id"],
            name=op.f(
                "fk_connector_track_artists_connector_artist_id_connector_artists"
            ),
            ondelete="SET NULL",
        ),
        # Also the writer's and the backfill's ON CONFLICT arbiter.
        sa.UniqueConstraint(
            "connector_track_id",
            "position",
            name="uq_connector_track_artists_track_position",
        ),
        sa.CheckConstraint(
            "position >= 0", name=op.f("ck_connector_track_artists_position_nonneg")
        ),
    )
    op.create_index(
        "ix_connector_track_artists_artist",
        "connector_track_artists",
        ["connector_artist_id"],
    )


def _create_artist_aliases() -> None:
    op.create_table(
        "artist_aliases",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("connector_artist_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("sort_name", sa.String(), nullable=True),
        sa.Column("alias_type", sa.String(32), nullable=True),
        sa.Column("locale", sa.String(16), nullable=True),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_artist_aliases")),
        sa.ForeignKeyConstraint(
            ["connector_artist_id"],
            ["connector_artists.id"],
            name=op.f("fk_artist_aliases_connector_artist_id_connector_artists"),
            ondelete="CASCADE",
        ),
        # NULLS NOT DISTINCT: Discogs' namevariations carry neither type nor
        # locale, so without it two untyped aliases of the same name would
        # both insert.
        sa.UniqueConstraint(
            "connector_artist_id",
            "name",
            "alias_type",
            "locale",
            name="uq_artist_aliases_identity",
            postgresql_nulls_not_distinct=True,
        ),
    )


def _create_migration_only_indexes() -> None:
    """Fuzzy-search, JSONB and functional indexes ``metadata.create_all`` cannot build."""
    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
    op.execute(
        sa.text(
            "CREATE INDEX ix_artists_name_trgm ON artists USING gin (name gin_trgm_ops)"
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX ix_connector_artists_name_trgm ON connector_artists "
            "USING gin (name gin_trgm_ops)"
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX ix_connector_artists_raw_metadata_gin "
            "ON connector_artists USING gin (raw_metadata)"
        )
    )
    # The alias lookup filters on ``lower(name)``; a plain index on ``name``
    # would never be used for it.
    op.execute(
        sa.text(
            "CREATE INDEX ix_artist_aliases_lower_name ON artist_aliases (lower(name))"
        )
    )


def upgrade() -> None:
    _create_artists()
    _create_connector_artists()
    _create_artist_mappings()
    _create_artist_favorites()
    _create_track_artists()
    _create_connector_track_artists()
    _create_artist_aliases()
    _create_migration_only_indexes()


def downgrade() -> None:
    """Drop everything in reverse dependency order.

    The policies go with their tables, so the explicit ``_disable_rls`` is
    only for the symmetry that makes an interrupted downgrade readable. The
    pg_trgm extension stays: 002_pg_opt created it and other indexes use it.
    """
    for index_name, _table in _MIGRATION_ONLY_INDEXES:
        op.execute(sa.text(f"DROP INDEX IF EXISTS {index_name}"))
    op.drop_table("artist_aliases")
    op.drop_table("connector_track_artists")
    for table in reversed(_RLS_TABLES):
        _disable_rls(table)
    op.drop_table("track_artists")
    op.drop_table("artist_favorites")
    op.drop_table("artist_mappings")
    op.drop_table("connector_artists")
    op.drop_table("artists")
