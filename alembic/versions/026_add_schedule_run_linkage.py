"""Link scheduled runs back to the schedule that fired them (v0.8.2).

Adds a nullable ``triggered_by_schedule_id`` FK to BOTH run-recording tables:
``workflow_runs`` (workflow schedules) and ``operation_runs`` (sync schedules).
There is no polymorphic FK — the column lives on each physical run table, and
whether the schedule targets a workflow or a sync (derived from ``workflow_id``)
tells a reader which table to query for its run history.

``ON DELETE SET NULL`` (not CASCADE): deleting a schedule must preserve its
historical runs, just orphaning the back-pointer. The reverse fast-path pointer
``schedules.last_run_id`` was created in migration 025.

Revision ID: 026_add_schedule_run_linkage
Revises: 025_add_schedules
Create Date: 2026-06-01
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "026_add_schedule_run_linkage"
down_revision: str | None = "025_add_schedules"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("workflow_runs", "operation_runs")


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(
            table,
            sa.Column("triggered_by_schedule_id", sa.UUID(), nullable=True),
        )
        op.create_foreign_key(
            op.f(f"fk_{table}_triggered_by_schedule_id_schedules"),
            table,
            "schedules",
            ["triggered_by_schedule_id"],
            ["id"],
            ondelete="SET NULL",
        )
        op.create_index(
            f"ix_{table}_triggered_by_schedule_id",
            table,
            ["triggered_by_schedule_id"],
        )


def downgrade() -> None:
    for table in _TABLES:
        op.drop_index(f"ix_{table}_triggered_by_schedule_id", table_name=table)
        op.drop_constraint(
            op.f(f"fk_{table}_triggered_by_schedule_id_schedules"),
            table,
            type_="foreignkey",
        )
        op.drop_column(table, "triggered_by_schedule_id")
