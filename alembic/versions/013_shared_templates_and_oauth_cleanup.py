"""Make workflow templates shared via nullable user_id + prune expired OAuth states.

Templates use user_id=NULL to mean "shared with all users". The RLS policy
on workflows is updated to allow SELECT/INSERT/UPDATE/DELETE on NULL rows
(defense-in-depth only — application layer controls template mutation).

Expired oauth_states rows are pruned as a one-time cleanup.

Revision ID: 013_shared_templates
Revises: 012_purge_default_user_data
"""

import sqlalchemy as sa

from alembic import op

revision = "013_shared_templates"
down_revision = "012_purge_default_user_data"


def upgrade() -> None:
    # 1. Make workflows.user_id nullable (templates use NULL = shared)
    op.alter_column(
        "workflows",
        "user_id",
        existing_type=sa.String(),
        nullable=True,
        server_default=None,
    )

    # 2. Set existing templates to NULL user_id
    op.execute(sa.text("UPDATE workflows SET user_id = NULL WHERE is_template = TRUE"))

    # 3. Update RLS policy on workflows to allow access to shared templates
    op.execute(sa.text("DROP POLICY IF EXISTS user_isolation ON workflows"))
    op.execute(
        sa.text(
            "CREATE POLICY user_isolation ON workflows "
            "FOR ALL USING ("
            "  user_id = current_setting('app.user_id', TRUE) "
            "  OR user_id IS NULL"
            ") WITH CHECK ("
            "  user_id = current_setting('app.user_id', TRUE) "
            "  OR user_id IS NULL"
            ")"
        )
    )

    # 4. Index on workflows.user_id for the OR user_id IS NULL query pattern
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_workflows_user_id ON workflows (user_id)"
        )
    )

    # 5. Prune expired OAuth states (one-time cleanup)
    op.execute(sa.text("DELETE FROM oauth_states WHERE expires_at < NOW()"))


def downgrade() -> None:
    op.drop_index("ix_workflows_user_id", "workflows")

    # Restore RLS policy without NULL clause
    op.execute(sa.text("DROP POLICY IF EXISTS user_isolation ON workflows"))
    op.execute(
        sa.text(
            "CREATE POLICY user_isolation ON workflows "
            "FOR ALL USING (user_id = current_setting('app.user_id', TRUE))"
        )
    )

    # Set templates back to 'default' user_id
    op.execute(
        sa.text("UPDATE workflows SET user_id = 'default' WHERE user_id IS NULL")
    )

    # Make user_id NOT NULL again
    op.alter_column(
        "workflows",
        "user_id",
        existing_type=sa.String(),
        nullable=False,
        server_default="default",
    )
