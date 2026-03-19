"""reorder_columns_id_first

Revision ID: e68ef4e64972
Revises: 3c3c6f0ed187
Create Date: 2025-08-17 00:37:42.947356

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e68ef4e64972'
down_revision: Union[str, None] = '3c3c6f0ed187'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Drop all tables to allow recreation with id column first."""
    
    # Get current table names
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()
    
    # Drop all tables except alembic_version
    for table_name in existing_tables:
        if table_name != 'alembic_version':
            op.execute(f"DROP TABLE IF EXISTS {table_name}")
    
    # Tables will be recreated with correct column order (id first due to sort_order=-1)
    # when the application runs init_db()


def downgrade() -> None:
    """Downgrade by dropping tables (same as upgrade since this is a structure-only change)."""
    
    # Get current table names
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()
    
    # Drop all tables except alembic_version
    for table_name in existing_tables:
        if table_name != 'alembic_version':
            op.execute(f"DROP TABLE IF EXISTS {table_name}")
    
    # Tables will be recreated with original column order when init_db() runs
