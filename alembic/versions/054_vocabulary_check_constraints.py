"""CHECK constraints pinning match_method and origin to their domain vocabularies.

``match_method`` and ``origin`` became domain vocabularies in v0.12.1 pre-flight
2 (``MatchMethod`` / ``MappingOrigin``), and the readers now raise on an
out-of-vocabulary row. Raising on read is late: the row is already stored, and
whichever writer produced it has moved on. These constraints refuse it at the
storage boundary instead.

The value lists are inlined rather than imported from the domain on purpose: a
migration is a frozen record of the schema at this revision, and one that read a
live ``frozenset`` would silently change meaning every time the vocabulary grows.
``tests/unit/migrations/test_054_vocabulary_check_constraints.py`` asserts the
lists below still equal the domain sets, so the two cannot drift unnoticed —
growing a vocabulary means a new migration that recreates the constraint.

Plain DDL, no ``CONCURRENTLY``/``NOT VALID``: the release command runs over the
Neon pooler URL, where ``autocommit_block`` cannot run (see 049 and 051). The
tables are small enough that a validating ACCESS EXCLUSIVE lock is cheap.

Revision ID: 054_vocabulary_check_constraints
Revises: 053_track_sort_indexes
Create Date: 2026-09-16
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "054_vocabulary_check_constraints"
down_revision: str | None = "053_track_sort_indexes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Sorted, so the rendered DDL is deterministic and matches the ORM's
# ``_vocabulary_check`` output character for character.
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


def _in_list(column_name: str, vocabulary: tuple[str, ...]) -> str:
    members = ", ".join(f"'{value}'" for value in vocabulary)
    return f"{column_name} IN ({members})"


# Fully qualified names (the ``ck_%(table_name)s_%(constraint_name)s``
# convention already applied), passed through ``op.f`` so Alembic does not
# apply it a second time — the 044 pattern.
_CK_MAPPING_METHOD = "ck_track_mappings_match_method_vocabulary"
_CK_MAPPING_ORIGIN = "ck_track_mappings_origin_vocabulary"
_CK_REVIEW_METHOD = "ck_match_reviews_match_method_vocabulary"


def upgrade() -> None:
    op.create_check_constraint(
        op.f(_CK_MAPPING_METHOD),
        "track_mappings",
        _in_list("match_method", MATCH_METHODS),
    )
    op.create_check_constraint(
        op.f(_CK_MAPPING_ORIGIN),
        "track_mappings",
        _in_list("origin", MAPPING_ORIGINS),
    )
    op.create_check_constraint(
        op.f(_CK_REVIEW_METHOD),
        "match_reviews",
        _in_list("match_method", MATCH_METHODS),
    )


def downgrade() -> None:
    op.drop_constraint(op.f(_CK_REVIEW_METHOD), "match_reviews", type_="check")
    op.drop_constraint(op.f(_CK_MAPPING_ORIGIN), "track_mappings", type_="check")
    op.drop_constraint(op.f(_CK_MAPPING_METHOD), "track_mappings", type_="check")
