"""Allow ``partial`` in the operation_runs status CHECK constraint.

018 pinned ``status IN ('running', 'complete', 'error', 'cancelled')``. A run that
did real work *and* recorded per-item failures — an import where most tracks
landed and a named few couldn't be resolved — is neither ``complete`` nor
``error``, and the audit row now records it as ``partial``. Without widening the
constraint the seam's finalize write fails and the run is left stuck at
``running``, which is worse than the imprecision it replaces.

No data migration: ``partial`` is a new state, not a rename, so every existing row
already holds a value the widened constraint accepts.

The downgrade folds ``partial`` back into ``error`` before narrowing, because the
old constraint would reject those rows.

Revision ID: 047_operation_run_partial
Revises: 046_resolution_events_mapping
Create Date: 2026-08-08
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "047_operation_run_partial"
down_revision: str | None = "046_resolution_events_mapping"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "operation_runs"
# Wrapped in ``op.f`` at every use site: the metadata naming convention prefixes
# bare constraint names with ``ck_<table>_``, so passing the full name unwrapped
# yields ``ck_operation_runs_ck_operation_runs_valid_status``. 018 created it the
# same way.
_CONSTRAINT = "ck_operation_runs_valid_status"
_WITH_PARTIAL = "status IN ('running', 'complete', 'partial', 'error', 'cancelled')"
_WITHOUT_PARTIAL = "status IN ('running', 'complete', 'error', 'cancelled')"


def _replace_status_constraint(condition: str) -> None:
    op.drop_constraint(op.f(_CONSTRAINT), _TABLE, type_="check")
    op.create_check_constraint(op.f(_CONSTRAINT), _TABLE, sa.text(condition))


def upgrade() -> None:
    _replace_status_constraint(_WITH_PARTIAL)


def downgrade() -> None:
    op.execute(
        sa.text(f"UPDATE {_TABLE} SET status = 'error' WHERE status = 'partial'")
    )
    _replace_status_constraint(_WITHOUT_PARTIAL)
