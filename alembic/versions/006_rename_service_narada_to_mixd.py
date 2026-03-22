"""Rename service 'narada' to 'mixd' in track data

Revision ID: 006_rename_service
Revises: f80e19f95cdd
Create Date: 2026-03-21

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "006_rename_service"
down_revision: str | None = "f80e19f95cdd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("UPDATE track_likes SET service = 'mixd' WHERE service = 'narada'")
    op.execute("UPDATE track_plays SET service = 'mixd' WHERE service = 'narada'")


def downgrade() -> None:
    op.execute("UPDATE track_likes SET service = 'narada' WHERE service = 'mixd'")
    op.execute("UPDATE track_plays SET service = 'narada' WHERE service = 'mixd'")
