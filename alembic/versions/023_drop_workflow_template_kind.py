"""Drop the read-only workflow "template" kind (is_template / source_template).

Eliminates the read-only template workflow kind. Built-in templates are now a
file-backed gallery (served from ``definitions/*.json`` and instantiated on use
via ``InstantiateWorkflowUseCase``), not persisted ``workflows`` rows. After this
migration the ``workflows`` table holds only user-owned, editable workflows.

Drops the ``is_template`` and ``source_template`` columns, the
``uq_workflows_source_template`` unique constraint, and the
``ix_workflows_is_template`` index, and deletes the seeded shared template rows
(``user_id IS NULL`` — see migration 013, which set templates to NULL owner).

PROD HAZARD: deleting ``user_id IS NULL`` rows cascade-deletes any
``workflow_runs`` / ``workflow_versions`` belonging to a template a user ran in
place (FK ``ON DELETE CASCADE``). Before applying to a populated database, check:

    SELECT count(*) FROM workflow_runs r
    JOIN workflows w ON r.workflow_id = w.id
    WHERE w.user_id IS NULL;

If that count is non-zero and the run history matters, reassign those runs to a
cloned user-owned workflow before upgrading.

Downgrade re-adds the columns/constraint/index (best-effort); it CANNOT restore
the deleted shared template rows.

Revision ID: 023_drop_workflow_template_kind
Revises: 022_user_scoped_mapping_uq
Create Date: 2026-05-30
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "023_drop_workflow_template_kind"
down_revision: str | None = "022_user_scoped_mapping_uq"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Remove the seeded shared template rows; cascades to their runs/versions.
    op.execute(sa.text("DELETE FROM workflows WHERE user_id IS NULL"))
    op.drop_index("ix_workflows_is_template", table_name="workflows")
    op.drop_constraint("uq_workflows_source_template", "workflows", type_="unique")
    op.drop_column("workflows", "source_template")
    op.drop_column("workflows", "is_template")


def downgrade() -> None:
    # Re-add columns/constraint/index. Deleted shared template rows are NOT restored.
    op.add_column(
        "workflows",
        sa.Column(
            "is_template",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "workflows",
        sa.Column("source_template", sa.String(length=100), nullable=True),
    )
    op.create_index("ix_workflows_is_template", "workflows", ["is_template"])
    op.create_unique_constraint(
        "uq_workflows_source_template", "workflows", ["source_template"]
    )
