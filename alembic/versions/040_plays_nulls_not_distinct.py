"""Harden play dedup constraints: NULLS NOT DISTINCT + duplicate collapse.

Both play dedup constraints include nullable ``ms_played``, and under Postgres
default semantics NULL ≠ NULL — so for rows with ``ms_played IS NULL`` (every
Last.fm scrobble: 99.6 % measured) ON CONFLICT never fires and a ``mode=full``
re-import would duplicate the entire history. Recreating the constraints with
``NULLS NOT DISTINCT`` (PG15+; all environments run PG17) closes the hole at
the schema layer without leaking a sentinel value into consumers
(play-import-convergence-findings.md §5c).

Two steps, in order:

1. **Duplicate-collapse pre-pass** — batched DELETEs OUTSIDE the migration
   transaction (``autocommit_block``), keeping the lowest ``id`` per
   exact-duplicate group of NULL-``ms_played`` rows. Every probed environment
   is currently clean (findings §1), but a database that re-imported before
   upgrading would hold duplicates, and constraint creation must not abort
   there. Batching keeps each commit bounded on such a database. Only
   NULL-``ms_played`` groups can hold duplicates: the old constraints already
   blocked exact duplicates for non-null values.

   RLS bracket (precedent: migration 035): the DELETEs run under ``NO FORCE
   ROW LEVEL SECURITY``, re-``FORCE``'d in a ``finally`` — autocommit means a
   mid-loop failure would not roll the bracket back transactionally.

2. **Constraint rebuild** — back inside the migration transaction, drop and
   recreate ``uq_track_plays_deduplication`` and
   ``uq_connector_plays_deduplication`` with ``postgresql_nulls_not_distinct``.
   ``bulk_insert_ignore_conflicts`` targets these via ``index_elements`` column
   lists; PG unique-index inference matches NULLS NOT DISTINCT indexes, so the
   repository layer needs no change.

Downgrade recreates the constraints with default NULL semantics. The collapse
pre-pass is not reversible (deleted duplicates are gone), which is the point.

Revision ID: 040_plays_nulls_not_distinct
Revises: 039_oauth_as_tables
Create Date: 2026-07-17
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from src.config.logging import get_logger

revision: str = "040_plays_nulls_not_distinct"
down_revision: str | None = "039_oauth_as_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_logger = get_logger(__name__)

_BATCH_SIZE = 10_000

_RLS_TABLES = ("track_plays", "connector_plays")

# Per table: the non-nullable constraint columns that define an exact-duplicate
# group among NULL-ms_played rows. Lowest id survives.
_COLLAPSE_KEYS: dict[str, tuple[str, ...]] = {
    "track_plays": ("user_id", "track_id", "service", "played_at"),
    "connector_plays": (
        "user_id",
        "connector_name",
        "connector_track_identifier",
        "played_at",
    ),
}

_CONSTRAINTS: dict[str, tuple[str, tuple[str, ...]]] = {
    "track_plays": (
        "uq_track_plays_deduplication",
        ("user_id", "track_id", "service", "played_at", "ms_played"),
    ),
    "connector_plays": (
        "uq_connector_plays_deduplication",
        (
            "user_id",
            "connector_name",
            "connector_track_identifier",
            "played_at",
            "ms_played",
        ),
    ),
}


def _collapse_null_ms_duplicates(table: str) -> int:
    """Batched delete of NULL-ms_played duplicate rows, keeping the lowest id."""
    bind = op.get_bind()
    join_on = " AND ".join(
        f"keeper.{col} = doomed.{col}" for col in _COLLAPSE_KEYS[table]
    )
    # DISTINCT: a doomed row with several lower-id keepers yields one join
    # tuple per keeper, so without it LIMIT counts tuples while DELETE's
    # rowcount counts rows — the loop would exit early on 3+-row groups and
    # leave duplicates for the constraint rebuild to trip over. Looping until
    # rowcount hits zero (rather than < batch) is the belt to that brace.
    stmt = sa.text(
        f"DELETE FROM {table} WHERE id IN ("
        f"  SELECT DISTINCT doomed.id FROM {table} doomed"
        f"  JOIN {table} keeper ON {join_on} AND keeper.id < doomed.id"
        f"  WHERE doomed.ms_played IS NULL AND keeper.ms_played IS NULL"
        f"  LIMIT {_BATCH_SIZE})"
    )
    total = 0
    while True:
        deleted = bind.execute(stmt).rowcount
        total += deleted
        if deleted == 0:
            return total


def upgrade() -> None:
    # Step 1 — collapse pre-pass, committed batch-by-batch outside the
    # migration transaction so a large dirty database never holds one giant
    # delete transaction (and constraint creation below cannot abort on dupes).
    with op.get_context().autocommit_block():
        for table in _RLS_TABLES:
            op.execute(sa.text(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY"))
        try:
            for table in _COLLAPSE_KEYS:
                collapsed = _collapse_null_ms_duplicates(table)
                if collapsed:
                    _logger.info(
                        "null_ms_duplicates_collapsed", table=table, rows=collapsed
                    )
        finally:
            for table in _RLS_TABLES:
                op.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))

    # Step 2 — rebuild both constraints with NULLS NOT DISTINCT.
    for table, (name, columns) in _CONSTRAINTS.items():
        op.drop_constraint(name, table, type_="unique")
        op.create_unique_constraint(
            name, table, list(columns), postgresql_nulls_not_distinct=True
        )


def downgrade() -> None:
    """Restore default NULL-distinct constraints; the collapse is not reversed."""
    for table, (name, columns) in _CONSTRAINTS.items():
        op.drop_constraint(name, table, type_="unique")
        op.create_unique_constraint(name, table, list(columns))
