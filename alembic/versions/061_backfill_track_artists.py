"""Expand ``tracks.artists`` and ``connector_tracks.artists`` JSONB into credit rows.

The expand half of expand/contract, for both credit tables. The JSONB columns
stay authoritative this cycle — the matching layer reads ``artist_normalized``
/ ``artists_text`` precomputed from them — so this migration only adds the
relational copies. The contract migration that drops the JSONB ships
separately, after the matching readers repoint and their characterization
tests pass.

The connector side runs in three set-based steps: mint ``connector_artists``
from the ``artists`` dumps the stored payloads carry, then expand each
``connector_tracks.artists`` name into a ``connector_track_artists`` row that
points at the record minted for the dump at the same position (NULL where the
dump has no id there). Both tables are global, so no RLS bracket is needed.

One set-based ``INSERT … SELECT``, no batching: production is ~82k tracks
(~100k credit rows), which is seconds. The statement itself lives in
``src.infrastructure.persistence.database.backfills`` because integration
tests build their schema with ``metadata.create_all`` and never run this
chain — SQL written inline here would be untestable.

Every credit lands with ``artist_id = NULL``, including the Various Artists
sentinel. The sentinel never becomes an artist row, and minting canonical
artists for the rest is the import's job.

RLS is bracketed off for the statement the way 052 does: the backfill writes
rows for every tenant in one statement, and ``FORCE ROW LEVEL SECURITY`` would
otherwise filter it down to whatever ``app.user_id`` happens to be.

``downgrade`` deletes every ``track_artists`` row rather than reversing
row-by-row. That is lossless this cycle precisely because the JSONB is still
authoritative: re-running ``upgrade`` rebuilds the same credits. It stops being
acceptable once the contract migration lands.

Plain DDL/DML, no ``CONCURRENTLY``: the release command runs over the Neon
pooler URL, where ``autocommit_block`` cannot run (see 049 and 051).

Revision ID: 061_backfill_track_artists
Revises: 060_artist_tables
Create Date: 2026-09-19
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from src.infrastructure.persistence.database.backfills import (
    backfill_connector_artists_sql,
    backfill_connector_track_artists_sql,
    backfill_track_artists_sql,
)

# revision identifiers, used by Alembic.
revision: str = "061_backfill_track_artists"
down_revision: str | None = "060_artist_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Read side and write side both need the bracket: the SELECT scans every
# tenant's tracks and the INSERT lands rows in every tenant.
_RLS_TABLES: tuple[str, ...] = ("tracks", "track_artists")


def upgrade() -> None:
    for table in _RLS_TABLES:
        op.execute(sa.text(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY"))
    try:
        _ = op.get_bind().execute(sa.text(backfill_track_artists_sql()))
    finally:
        for table in _RLS_TABLES:
            op.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
    _ = op.get_bind().execute(sa.text(backfill_connector_artists_sql()))
    _ = op.get_bind().execute(sa.text(backfill_connector_track_artists_sql()))


def downgrade() -> None:
    """Empty both credit tables; the JSONB columns rebuild them on the way up.

    The ``connector_artists`` records minted on the way up stay: an import
    since may have mapped them, and they are the service's facts either way.
    """
    op.execute(sa.text("DELETE FROM connector_track_artists"))
    op.execute(sa.text("ALTER TABLE track_artists NO FORCE ROW LEVEL SECURITY"))
    try:
        op.execute(sa.text("DELETE FROM track_artists"))
    finally:
        op.execute(sa.text("ALTER TABLE track_artists FORCE ROW LEVEL SECURITY"))
