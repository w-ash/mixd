"""Rename play-history day-window config keys to self-documenting names.

``min_days_back`` / ``max_days_back`` encoded age backwards (min = *older than*,
max = *within*) and shipped inverted editor labels once. Renamed to
``not_played_in_days`` / ``played_within_days`` so a field name states its
behavior. Clean break — no compatibility shim — so saved workflow definitions
must be rewritten in place.

The same ``WorkflowDef`` JSON shape lives in three JSONB columns; all are
rewritten so the engine never reads an old-key definition:
- ``workflows.definition`` (live, editable)
- ``workflow_versions.definition`` (prior-version snapshots)
- ``workflow_runs.definition_snapshot`` (frozen run snapshots)

Per-task surgery: walk ``definition->'tasks'`` and rename the keys inside each
task ``config``. No precedent for a JSONB *key* rewrite — prior renames (006,
017) were scalar column-value UPDATEs — so the transform is a small pure
function (``_rename_definition_keys``, unit-tested) wrapped in a row loop.

Revision ID: 033_rename_day_window_keys
Revises: 032_operation_run_request_params
Create Date: 2026-06-29
"""

from collections.abc import Mapping, Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision: str = "033_rename_day_window_keys"
down_revision: str | None = "032_operation_run_request_params"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (table, JSONB column) pairs holding a WorkflowDef shape. Fixed internal
# allowlist — never user input.
_TARGETS: tuple[tuple[str, str], ...] = (
    ("workflows", "definition"),
    ("workflow_versions", "definition"),
    ("workflow_runs", "definition_snapshot"),
)

_UPGRADE_RENAME: Mapping[str, str] = {
    "min_days_back": "not_played_in_days",
    "max_days_back": "played_within_days",
}
_DOWNGRADE_RENAME: Mapping[str, str] = {v: k for k, v in _UPGRADE_RENAME.items()}


def _rename_definition_keys(definition: object, rename: Mapping[str, str]) -> bool:
    """Rename day-window keys inside every task ``config`` of a WorkflowDef dict.

    Mutates ``definition`` in place; returns True if any key was renamed (so the
    caller can skip a no-op UPDATE). Pure and defensive — non-dict definitions,
    tasks, or configs are skipped rather than raising, so a malformed stored row
    can't break the migration.
    """
    if not isinstance(definition, dict):
        return False
    changed = False
    tasks = definition.get("tasks")
    if not isinstance(tasks, list):
        return False
    for task in tasks:
        if not isinstance(task, dict):
            continue
        config = task.get("config")
        if not isinstance(config, dict):
            continue
        for old, new in rename.items():
            if old in config:
                config[new] = config.pop(old)
                changed = True
    return changed


def _rewrite(rename: Mapping[str, str]) -> None:
    bind = op.get_bind()
    meta = sa.MetaData()
    for table_name, column in _TARGETS:
        table = sa.Table(
            table_name,
            meta,
            sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
            sa.Column(column, pg.JSONB),
        )
        rows = bind.execute(sa.select(table.c.id, table.c[column])).all()
        for row_id, definition in rows:
            if _rename_definition_keys(definition, rename):
                bind.execute(
                    sa
                    .update(table)
                    .where(table.c.id == row_id)
                    .values({column: definition})
                )


def upgrade() -> None:
    _rewrite(_UPGRADE_RENAME)


def downgrade() -> None:
    _rewrite(_DOWNGRADE_RENAME)
