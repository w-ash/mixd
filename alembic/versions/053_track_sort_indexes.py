"""User-scoped covering indexes for every track sort (v0.10.4.1).

Measured against prod (57,248 tracks): only `title`, `plays_desc` and
`last_played_desc` were index-ordered (~0.2ms). Every other sort fell back to
a sequential scan plus a full sort of the user's library — `added` 44-49ms,
`duration` 44-47ms, `last_played_asc` 44ms — growing linearly with the library.

Two distinct causes, both fixed here:

1. **`created_at` and `duration_ms` had no usable index at all.** Migration 003
   declared `ix_tracks_created_at_id`, but it is absent from prod; whatever the
   history, the codebase believed in an index the database does not have. Those
   003 indexes also predate multi-tenancy and lead with the sort column rather
   than `user_id`, so they are dropped rather than recreated.

2. **`NULLS LAST` is free ascending and index-defeating descending.** It is
   Postgres's default for ASC and the opposite of the default for DESC, so one
   index cannot serve both directions of a nullable sort — a backward scan of
   `col DESC NULLS LAST` yields `ASC NULLS FIRST`. The two nullable sort columns
   (`duration_ms`, `last_played_at`) therefore get one index per direction.

Every index carries `id` as the final key because the ORDER BY always ends with
the `id` tiebreaker and the keyset predicate compares `(col, id)` as a row. On
`play_count` this is the whole ballgame: the two largest tie groups are 19,338
rows at 1 play and 18,205 at 0, so `plays_asc` was sorting ~18k rows by `id` to
return 50 (12.4ms).

Revision ID: 053_track_sort_indexes
Revises: 052_track_play_aggregates
Create Date: 2026-08-21
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "053_track_sort_indexes"
down_revision: str | None = "052_track_play_aggregates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (index_name, columns) — ASC entries rely on Postgres's ASC default of
# NULLS LAST, which is exactly what the repository's ORDER BY asks for.
_NEW_INDEXES: list[tuple[str, list[str | sa.TextClause]]] = [
    ("ix_tracks_user_title_id", ["user_id", "title", "id"]),
    ("ix_tracks_user_created_at_id", ["user_id", "created_at", "id"]),
    ("ix_tracks_user_play_count_id", ["user_id", "play_count", "id"]),
    # Nullable columns lead with the NULL-ness boolean, matching the ORDER BY
    # the repository emits. `(col IS NULL) ASC` and `(col IS NOT NULL) DESC`
    # both sort NULLs last, and keeping every key in one direction is what lets
    # the keyset row-comparison seek instead of filter.
    (
        "ix_tracks_user_duration_id",
        ["user_id", sa.text("(duration_ms IS NULL)"), "duration_ms", "id"],
    ),
    (
        "ix_tracks_user_duration_desc_id",
        [
            "user_id",
            sa.text("(duration_ms IS NOT NULL) DESC"),
            sa.text("duration_ms DESC"),
            sa.text("id DESC"),
        ],
    ),
    (
        "ix_tracks_user_last_played_id",
        ["user_id", sa.text("(last_played_at IS NULL)"), "last_played_at", "id"],
    ),
    (
        "ix_tracks_user_last_played_desc_id",
        [
            "user_id",
            sa.text("(last_played_at IS NOT NULL) DESC"),
            sa.text("last_played_at DESC"),
            sa.text("id DESC"),
        ],
    ),
]

# Superseded by the `_id`-suffixed versions above.
_SUPERSEDED = [
    "ix_tracks_user_play_count",
    "ix_tracks_user_last_played",
]

# Migration 003's pre-tenancy keyset indexes. Absent in prod, present in
# freshly-migrated databases (tests) — dropped with if_exists so both converge.
# ``ix_tracks_artists_text_id`` is dead outright: the artist sort is withdrawn
# until artists are first-class, because ordering by the joined ``artists_text``
# display string sorts by "Bowie, Eno" rather than by artist.
_PRE_TENANCY = [
    "ix_tracks_title_id",
    "ix_tracks_created_at_id",
    "ix_tracks_artists_text_id",
]


def upgrade() -> None:
    for name, columns in _NEW_INDEXES:
        op.create_index(name, "tracks", columns, if_not_exists=True)
    for name in _SUPERSEDED + _PRE_TENANCY:
        op.drop_index(name, table_name="tracks", if_exists=True)


def downgrade() -> None:
    op.create_index(
        "ix_tracks_user_last_played",
        "tracks",
        ["user_id", sa.text("last_played_at DESC NULLS LAST")],
        if_not_exists=True,
    )
    op.create_index(
        "ix_tracks_user_play_count",
        "tracks",
        ["user_id", "play_count"],
        if_not_exists=True,
    )
    op.create_index("ix_tracks_title_id", "tracks", ["title", "id"], if_not_exists=True)
    op.create_index(
        "ix_tracks_created_at_id", "tracks", ["created_at", "id"], if_not_exists=True
    )
    op.create_index(
        "ix_tracks_artists_text_id",
        "tracks",
        ["artists_text", "id"],
        if_not_exists=True,
    )
    for name, _ in _NEW_INDEXES:
        op.drop_index(name, table_name="tracks", if_exists=True)
