"""add output_tracks and node_details JSON columns

Revision ID: 3faa2309339e
Revises: 0f3cf2f1c012
Create Date: 2026-03-08 00:21:49.676966

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3faa2309339e"
down_revision: str | None = "0f3cf2f1c012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("workflows", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "definition_version", sa.Integer(), nullable=False, server_default="1"
            )
        )

    with op.batch_alter_table("workflow_runs", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "definition_version", sa.Integer(), nullable=False, server_default="1"
            )
        )
        batch_op.add_column(sa.Column("output_tracks", sa.JSON(), nullable=True))

    with op.batch_alter_table("workflow_run_nodes", schema=None) as batch_op:
        batch_op.add_column(sa.Column("node_details", sa.JSON(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("workflow_run_nodes", schema=None) as batch_op:
        batch_op.drop_column("node_details")

    with op.batch_alter_table("workflow_runs", schema=None) as batch_op:
        batch_op.drop_column("output_tracks")
        batch_op.drop_column("definition_version")

    with op.batch_alter_table("workflows", schema=None) as batch_op:
        batch_op.drop_column("definition_version")
