"""Add user_settings table

Revision ID: f80e19f95cdd
Revises: 005_oauth_tokens
Create Date: 2026-03-19 00:18:01.585717

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'f80e19f95cdd'
down_revision: str | None = '005_oauth_tokens'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add user_settings table for persistent user preferences."""
    op.create_table('user_settings',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('key', sa.String(length=64), nullable=False),
    sa.Column('settings', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_user_settings')),
    sa.UniqueConstraint('key', name='uq_user_settings_key')
    )


def downgrade() -> None:
    """Remove user_settings table."""
    op.drop_table('user_settings')
