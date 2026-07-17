"""Convert ix_connector_plays_unresolved to a partial index.

The index exists solely to find ledger rows that still need resolution
(``resolved_track_id IS NULL``) — with resolution write-back landing in
v0.10.0, resolved rows are the overwhelming majority and indexing them here
is pure waste. A partial index keeps only the unresolved tail, shrinking the
index and making the unresolved-backlog query a targeted scan.

Revision ID: 041_unresolved_partial_index
Revises: 040_plays_nulls_not_distinct
Create Date: 2026-07-17
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "041_unresolved_partial_index"
down_revision: str | None = "040_plays_nulls_not_distinct"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX = "ix_connector_plays_unresolved"
_TABLE = "connector_plays"
_COLUMNS = ["connector_name", "resolved_track_id"]


def upgrade() -> None:
    op.drop_index(_INDEX, table_name=_TABLE)
    op.create_index(
        _INDEX,
        _TABLE,
        _COLUMNS,
        postgresql_where=sa.text("resolved_track_id IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(_INDEX, table_name=_TABLE)
    op.create_index(_INDEX, _TABLE, _COLUMNS)
