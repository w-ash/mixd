"""Index resolution_events for the one query that reads it (v0.10.2).

045 gave the event log three indexes on a guess about how it would be read. One
release of hindsight says the guess was inverted in both directions.

The **only** read of ``resolution_events`` is ``events_for_mapping`` — "why does
my library believe this mapping?", ``WHERE user_id = ? AND resulting_mapping_id
= ? ORDER BY recorded_at DESC LIMIT 100``. No index led with that pair, so the
planner fell back to ``ix_resolution_events_user_time`` and filtered the
tenant's entire timeline to find the handful of rows about one mapping — work
that grows with the tenant's history rather than with the answer. The
replacement leads ``(user_id, resulting_mapping_id)`` and carries
``recorded_at DESC`` so the ORDER BY and the LIMIT are both satisfied by the
scan. It is *partial* because most events name no mapping at all: only
accept/supersede-shaped decisions produce one, and rows with a NULL there can
never satisfy an equality predicate on it, so keeping them costs writes and
buys nothing.

``ix_resolution_events_matcher_version`` goes, having acquired no readers: the
cross-tenant "which decisions did matcher X make" query it was built for was
never written, and ``active_rejected_pairs`` — the one place that does filter on
a matcher version — reads ``resolution_negatives`` instead. Until a calibration
query exists to justify it, it is a second btree maintained on the matching
pipeline's hottest insert path in exchange for nothing. Its definition survives
verbatim in this file's ``downgrade``, so restoring it later is a copy rather
than an archaeology.

Revision ID: 046_resolution_events_mapping
Revises: 045_resolution_events
Create Date: 2026-07-31

(The id is shorter than this file's name on purpose: Alembic's
``alembic_version.version_num`` is ``VARCHAR(32)``, and an id that overflows it
fails at the *end* of an otherwise successful upgrade, after the DDL has run.)
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "046_resolution_events_mapping"
down_revision: str | None = "045_resolution_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_EVENTS = "resolution_events"
_MAPPING_INDEX = "ix_resolution_events_mapping"
_MATCHER_INDEX = "ix_resolution_events_matcher_version"


def _create_mapping_index() -> None:
    op.create_index(
        _MAPPING_INDEX,
        _EVENTS,
        ["user_id", "resulting_mapping_id", sa.text("recorded_at DESC")],
        postgresql_where=sa.text("resulting_mapping_id IS NOT NULL"),
    )


def _create_matcher_index() -> None:
    """Exactly as 045 built it — a downgrade must land on 045's schema, not a
    paraphrase of it."""
    op.create_index(
        _MATCHER_INDEX,
        _EVENTS,
        ["matcher_version", sa.text("recorded_at DESC")],
    )


def upgrade() -> None:
    _create_mapping_index()
    op.drop_index(_MATCHER_INDEX, table_name=_EVENTS)


def downgrade() -> None:
    _create_matcher_index()
    op.drop_index(_MAPPING_INDEX, table_name=_EVENTS)
