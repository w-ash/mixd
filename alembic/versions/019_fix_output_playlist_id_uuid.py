"""Fix workflow_runs.output_playlist_id type: integer → uuid.

The DB model declares ``output_playlist_id: Mapped[UuidType | None]`` but the
initial schema (``001_initial_schema``) created the column as ``Integer`` and
no subsequent migration altered it. Production INSERTs of ``workflow_runs``
fail with ``DatatypeMismatch`` (UUID parameter vs. integer column).

The column has never been populated in production and has no FK constraint,
so a direct ``ALTER COLUMN ... TYPE uuid USING NULL::uuid`` is safe.

Revision ID: 019_output_playlist_id_uuid
Revises: 018_operation_runs
Create Date: 2026-05-09
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "019_output_playlist_id_uuid"
down_revision: str | None = "018_operation_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "workflow_runs",
        "output_playlist_id",
        existing_type=sa.Integer(),
        type_=sa.UUID(),
        existing_nullable=True,
        postgresql_using="NULL::uuid",
    )


def downgrade() -> None:
    op.alter_column(
        "workflow_runs",
        "output_playlist_id",
        existing_type=sa.UUID(),
        type_=sa.Integer(),
        existing_nullable=True,
        postgresql_using="NULL::integer",
    )
