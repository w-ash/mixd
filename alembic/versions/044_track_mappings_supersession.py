"""Make track mappings append-only: supersession columns + live-scoped uniqueness.

An identity decision is currently overwritten in place, so "we used to believe
X" is unrecoverable and a bad flip is indistinguishable from a correction. This
migration turns ``track_mappings`` into an append-only table: a changed
re-assertion retires the incumbent (``superseded_at`` + ``supersession_reason``,
pointing at its successor via ``superseded_by_id``) and inserts a new live row.
Uniqueness therefore has to become *live* uniqueness — a full unique constraint
cannot coexist with retained history.

Four steps, in order:

1. **Pre-pass** — batched DML OUTSIDE the migration transaction
   (``autocommit_block``), so a large dirty database never holds one giant
   transaction and the constraint work below cannot abort on data it could
   have repaired.

   a. *Duplicate collapse* on ``(user_id, connector_track_id, connector_name)``,
      lowest ``id`` surviving. Defensive: the constraint being replaced already
      forbids these, but the partial unique index in step 3 must not be the
      thing that discovers a database where the constraint was ever missing.
   b. *FM4d denorm repair* (prod-confirmed 2026-07-27: 321 tracks whose
      ``spotify_id`` disagreed with their primary mapping + 45 with no spotify
      mapping at all). Two passes per denormalized column, and the order
      matters: pass 1 NULLs every disagreeing value, pass 2 assigns from the
      primary live mapping. Repairing in one pass would trip
      ``uq_tracks_user_spotify_id`` on any pair of tracks holding each other's
      identifier — clearing first frees every contested value.

   RLS bracket (precedent: migrations 035 + 040): the DML runs under ``NO FORCE
   ROW LEVEL SECURITY``, re-``FORCE``'d in a ``finally`` — autocommit means a
   mid-loop failure would not roll the bracket back transactionally.

2. **Supersession columns** on ``track_mappings``. ``superseded_by_id`` is a
   self-FK, ``ON DELETE SET NULL`` (a deleted successor must not dangle) and
   **DEFERRABLE INITIALLY DEFERRED** — the write path stamps a predecessor with
   a successor id in the same statement batch that later inserts the successor.
   That is legal alongside ON CONFLICT because the arbiter is the unique
   *index*, not this constraint (only arbiters must be non-deferrable).
   ``superseded_by_id`` stays nullable: a retirement can have no replacement
   (``id_dead`` with nothing to relink to), which is exactly what the CHECK
   encodes — the *timestamp* and *reason* move as a unit, the successor is
   optional.

3. **Live-scoped uniqueness + read paths.** The full unique constraint becomes
   a partial unique index over live rows only; ``uq_primary_mapping`` gains the
   same predicate. Two new partial indexes serve the live reader
   (``user_id, track_id``) and the rare chain walk (``superseded_by_id``). The
   autovacuum scale factor drops to 0.02 because supersession UPDATEs write an
   indexed column — those updates can never be HOT, so dead tuples accumulate
   faster than the 20 % default would reclaim them.

4. **Downgrade is lossy and deliberately so**: superseded rows cannot coexist
   with the restored full unique constraint, so they are deleted. History that
   only ``track_mappings`` held is gone — downgrading past this revision is a
   one-way loss of the audit trail (same posture as 040's collapse pre-pass).
   The FM4d repair is likewise not reversed; re-introducing stale
   denormalized ids would be vandalism, not a rollback.

Revision ID: 044_track_mappings_supersession
Revises: 043_adaptive_polling
Create Date: 2026-07-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from src.config.logging import get_logger

revision: str = "044_track_mappings_supersession"
down_revision: str | None = "043_adaptive_polling"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_logger = get_logger(__name__)

_BATCH_SIZE = 10_000

_TABLE = "track_mappings"

# Both tables carry FORCE ROW LEVEL SECURITY (migration 011) and the pre-pass
# writes to both.
_RLS_TABLES = ("track_mappings", "tracks")

# The live-identity key: one live mapping per user per connector track.
_LIVE_KEY = ("user_id", "connector_track_id", "connector_name")

_LIVE_UNIQUE = "uq_track_mappings_live_connector"
_FULL_UNIQUE = "uq_track_mappings_user_connector"
_PRIMARY_UNIQUE = "uq_primary_mapping"
_PRIMARY_KEY_COLUMNS = ["user_id", "track_id", "connector_name"]
_LIVE_TRACK_INDEX = "ix_track_mappings_live_track"
_SUPERSEDED_BY_INDEX = "ix_track_mappings_superseded_by"
_FK_SUPERSEDED_BY = "fk_track_mappings_superseded_by_id_track_mappings"
_CK_COHERENT = "ck_track_mappings_supersession_coherent"

# Mirrors DenormalizedTrackColumns.COLUMN_MAP (src/config/constants.py): the
# fast-path columns on ``tracks`` that must agree with the primary live mapping.
_DENORM_COLUMNS: tuple[tuple[str, str], ...] = (
    ("spotify", "spotify_id"),
    ("musicbrainz", "mbid"),
)

# The three supersession columns move as a unit; the successor pointer is
# optional (retirement with no replacement).
_COHERENCE_CHECK = (
    "(superseded_at IS NULL AND superseded_by_id IS NULL "
    "AND supersession_reason IS NULL) "
    "OR (superseded_at IS NOT NULL AND supersession_reason IS NOT NULL)"
)


def _collapse_duplicate_live_keys() -> int:
    """Batched delete of duplicate ``_LIVE_KEY`` rows, keeping the lowest id."""
    bind = op.get_bind()
    join_on = " AND ".join(f"keeper.{col} = doomed.{col}" for col in _LIVE_KEY)
    # DISTINCT (040's lesson): a doomed row with several lower-id keepers yields
    # one join tuple per keeper, so without it LIMIT counts tuples while DELETE's
    # rowcount counts rows and the loop exits early on 3+-row groups. Looping
    # until rowcount hits zero is the belt to that brace.
    stmt = sa.text(
        f"DELETE FROM {_TABLE} WHERE id IN ("
        f"  SELECT DISTINCT doomed.id FROM {_TABLE} doomed"
        f"  JOIN {_TABLE} keeper ON {join_on} AND keeper.id < doomed.id"
        f"  LIMIT {_BATCH_SIZE})"
    )
    total = 0
    while True:
        deleted = bind.execute(stmt).rowcount
        total += deleted
        if deleted == 0:
            return total


def _primary_identifier_sql(alias: str) -> str:
    """Scalar subquery: the connector identifier of ``alias``'s primary mapping.

    At most one row by ``uq_primary_mapping`` (one primary per user-track-
    connector); no rows — hence SQL NULL — when the track has no primary
    mapping for the connector at all.

    ``ORDER BY id LIMIT 1`` anyway: this pre-pass runs *before* the constraint
    work, on exactly the databases whose invariants are in doubt (that is why
    the duplicate collapse above exists), and a scalar subquery that returns
    two rows aborts the whole migration. Deterministic by id so a retry repairs
    to the same value.
    """
    return (
        "SELECT ct.connector_track_identifier "
        "FROM track_mappings tm "
        "JOIN connector_tracks ct ON ct.id = tm.connector_track_id "
        f"WHERE tm.track_id = {alias}.id AND tm.user_id = {alias}.user_id "
        "AND tm.connector_name = :connector AND tm.is_primary "
        "ORDER BY tm.id LIMIT 1"
    )


def _run_batched(stmt: sa.TextClause, params: dict[str, str]) -> int:
    """Run a self-terminating batched DML statement until it affects no rows."""
    bind = op.get_bind()
    total = 0
    while True:
        affected = bind.execute(stmt, params).rowcount
        total += affected
        if affected == 0:
            return total


def _repair_denormalized_column(connector: str, column: str) -> tuple[int, int]:
    """Re-derive ``tracks.<column>`` from the primary mapping; return (cleared, set)."""
    params = {"connector": connector}
    # Pass 1 — clear every value that disagrees with the primary mapping,
    # including the "column set but no mapping exists" arm (the subquery is
    # NULL there, and ``x IS DISTINCT FROM NULL`` is true for any non-null x).
    cleared = _run_batched(
        sa.text(
            f"UPDATE tracks AS t SET {column} = NULL WHERE t.id IN ("
            f"  SELECT s.id FROM tracks s"
            f"  WHERE s.{column} IS NOT NULL"
            f"    AND s.{column} IS DISTINCT FROM ({_primary_identifier_sql('s')})"
            f"  LIMIT {_BATCH_SIZE})"
        ),
        params,
    )
    # Pass 2 — assign from the primary mapping. Collision-free: a connector
    # track belongs to exactly one canonical track per user, so no two tracks
    # can claim the same identifier.
    assigned = _run_batched(
        sa.text(
            f"UPDATE tracks AS t SET {column} = ({_primary_identifier_sql('t')}) "
            f"WHERE t.id IN ("
            f"  SELECT s.id FROM tracks s"
            f"  WHERE s.{column} IS NULL"
            f"    AND ({_primary_identifier_sql('s')}) IS NOT NULL"
            f"  LIMIT {_BATCH_SIZE})"
        ),
        params,
    )
    return cleared, assigned


def _pre_pass() -> None:
    """Collapse duplicate live keys, then re-derive the denormalized id columns."""
    collapsed = _collapse_duplicate_live_keys()
    if collapsed:
        _logger.info("duplicate_mappings_collapsed", table=_TABLE, rows=collapsed)
    for connector, column in _DENORM_COLUMNS:
        cleared, assigned = _repair_denormalized_column(connector, column)
        if cleared or assigned:
            _logger.info(
                "denormalized_id_repaired",
                connector=connector,
                column=column,
                cleared=cleared,
                assigned=assigned,
            )


def upgrade() -> None:
    # Step 0 — heal a leaked bracket. The pre-pass below runs in autocommit, so
    # a crash between its NO FORCE and the ``finally`` that restores FORCE
    # leaves the tables permanently un-forced: RLS still "enabled", silently
    # not applied to the table owner. Re-asserting FORCE unconditionally costs
    # nothing on a healthy database and repairs the one state a retry of this
    # migration would otherwise inherit and never notice.
    for table in _RLS_TABLES:
        op.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))

    # Step 1 — pre-pass, committed batch-by-batch outside the migration
    # transaction. NO FORCE bracket: the migration role owns these tables and
    # FORCE would apply the tenant policy to a cross-tenant repair.
    with op.get_context().autocommit_block():
        for table in _RLS_TABLES:
            op.execute(sa.text(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY"))
        try:
            _pre_pass()
        finally:
            for table in _RLS_TABLES:
                op.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))

    # Step 2 — supersession columns.
    op.add_column(
        _TABLE,
        sa.Column("superseded_by_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        _TABLE, sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        _TABLE, sa.Column("supersession_reason", sa.String(32), nullable=True)
    )
    op.add_column(_TABLE, sa.Column("supersession_scope", sa.String(64), nullable=True))
    op.add_column(
        _TABLE, sa.Column("next_verify_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_foreign_key(
        op.f(_FK_SUPERSEDED_BY),
        _TABLE,
        _TABLE,
        ["superseded_by_id"],
        ["id"],
        ondelete="SET NULL",
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_check_constraint(op.f(_CK_COHERENT), _TABLE, sa.text(_COHERENCE_CHECK))

    # Step 3 — uniqueness becomes live uniqueness. IF EXISTS for the same
    # reason the collapse pre-pass exists: a database that lost this constraint
    # is a state to migrate, not a state to crash on.
    op.execute(
        sa.text(f"ALTER TABLE {_TABLE} DROP CONSTRAINT IF EXISTS {_FULL_UNIQUE}")
    )
    op.create_index(
        _LIVE_UNIQUE,
        _TABLE,
        list(_LIVE_KEY),
        unique=True,
        postgresql_where=sa.text("superseded_at IS NULL"),
    )
    # IF EXISTS for the same reason the constraint above gets it: a database
    # that lost this index is a state to migrate, not a state to crash on, and
    # ``op.drop_index`` has no such option.
    op.execute(sa.text(f"DROP INDEX IF EXISTS {_PRIMARY_UNIQUE}"))
    op.create_index(
        _PRIMARY_UNIQUE,
        _TABLE,
        _PRIMARY_KEY_COLUMNS,
        unique=True,
        postgresql_where=sa.text("is_primary = TRUE AND superseded_at IS NULL"),
    )
    op.create_index(
        _LIVE_TRACK_INDEX,
        _TABLE,
        ["user_id", "track_id"],
        postgresql_where=sa.text("superseded_at IS NULL"),
    )
    op.create_index(
        _SUPERSEDED_BY_INDEX,
        _TABLE,
        ["superseded_by_id"],
        postgresql_where=sa.text("superseded_by_id IS NOT NULL"),
    )
    # Supersession UPDATEs write superseded_at, which is indexed — so they can
    # never take the HOT path and every one leaves a dead tuple behind. 2 %
    # keeps the partial indexes from bloating between vacuums.
    op.execute(
        sa.text(f"ALTER TABLE {_TABLE} SET (autovacuum_vacuum_scale_factor = 0.02)")
    )


def downgrade() -> None:
    """Restore full uniqueness. Superseded rows are DELETED — see the docstring."""
    op.execute(sa.text(f"ALTER TABLE {_TABLE} RESET (autovacuum_vacuum_scale_factor)"))

    # Superseded rows cannot coexist with the restored full unique constraint.
    # Same NO FORCE bracket as the pre-pass; here it is transactional, so a
    # failure rolls the whole migration back.
    op.execute(sa.text(f"ALTER TABLE {_TABLE} NO FORCE ROW LEVEL SECURITY"))
    op.execute(sa.text(f"DELETE FROM {_TABLE} WHERE superseded_at IS NOT NULL"))
    op.execute(sa.text(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY"))

    op.drop_index(_SUPERSEDED_BY_INDEX, table_name=_TABLE)
    op.drop_index(_LIVE_TRACK_INDEX, table_name=_TABLE)
    op.drop_index(_PRIMARY_UNIQUE, table_name=_TABLE)
    op.create_index(
        _PRIMARY_UNIQUE,
        _TABLE,
        _PRIMARY_KEY_COLUMNS,
        unique=True,
        postgresql_where=sa.text("is_primary = TRUE"),
    )
    op.drop_index(_LIVE_UNIQUE, table_name=_TABLE)
    op.create_unique_constraint(_FULL_UNIQUE, _TABLE, list(_LIVE_KEY))

    op.drop_constraint(op.f(_CK_COHERENT), _TABLE, type_="check")
    op.drop_constraint(op.f(_FK_SUPERSEDED_BY), _TABLE, type_="foreignkey")
    for column in (
        "next_verify_at",
        "supersession_scope",
        "supersession_reason",
        "superseded_at",
        "superseded_by_id",
    ):
        op.drop_column(_TABLE, column)
