"""Widen the ``match_method`` CHECK constraints for ``mb_url_rel``.

Artist resolution seeds its first mappings from MusicBrainz ``url-rels``: once
an artist has an MBID, its Spotify, Discogs, Apple and Tidal ids arrive in the
same lookup, and those become ``artist_mappings`` rows before any name matching
runs. That decision needs a name in the vocabulary, and the three
``match_method`` CHECK constraints written by 054 and 060 would refuse it.

Constraints cannot be widened in place, so each is dropped and recreated with
the full sorted list. The earlier migrations keep their own inlined lists
untouched — a migration is a frozen record of the schema at its revision, and
editing 054 would rewrite history for every database that already ran it. From
here on this migration owns the live ``match_method`` vocabulary, and
``tests/unit/migrations/test_062_mb_url_rel_match_method.py`` is what notices
the next drift.

Plain DDL, no ``CONCURRENTLY``/``NOT VALID``: the release command runs over the
Neon pooler URL, where ``autocommit_block`` cannot run (see 049 and 051). The
tables are small enough that a validating ACCESS EXCLUSIVE lock is cheap.

Revision ID: 062_mb_url_rel_match_method
Revises: 061_backfill_track_artists
Create Date: 2026-09-19
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "062_mb_url_rel_match_method"
down_revision: str | None = "061_backfill_track_artists"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Sorted, so the rendered DDL is deterministic and matches the ORM's
# ``vocabulary_check`` output character for character.
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
    "mb_url_rel",
    "mbid",
    "mbid_match",
    "search_fallback",
    "search_fallback_stale_id",
    "spotify_connector_play_resolver",
    "spotify_redirect",
)

# The list before ``mb_url_rel`` — what ``downgrade`` restores.
_PREVIOUS_MATCH_METHODS: tuple[str, ...] = tuple(
    method for method in MATCH_METHODS if method != "mb_url_rel"
)

# Fully qualified names (the ``ck_%(table_name)s_%(constraint_name)s``
# convention already applied), passed through ``op.f`` so Alembic does not
# apply it a second time — the 054 pattern.
_CHECKS: tuple[tuple[str, str], ...] = (
    ("track_mappings", "ck_track_mappings_match_method_vocabulary"),
    ("match_reviews", "ck_match_reviews_match_method_vocabulary"),
    ("artist_mappings", "ck_artist_mappings_match_method_vocabulary"),
)


def _in_list(column_name: str, vocabulary: tuple[str, ...]) -> str:
    members = ", ".join(f"'{value}'" for value in vocabulary)
    return f"{column_name} IN ({members})"


def _recreate(vocabulary: tuple[str, ...]) -> None:
    for table, constraint in _CHECKS:
        op.drop_constraint(op.f(constraint), table, type_="check")
        op.create_check_constraint(
            op.f(constraint), table, _in_list("match_method", vocabulary)
        )


def upgrade() -> None:
    _recreate(MATCH_METHODS)


def downgrade() -> None:
    _recreate(_PREVIOUS_MATCH_METHODS)
