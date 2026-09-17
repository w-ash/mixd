"""Align hand-written column defaults with the ORM so autogenerate is empty.

``tests/integration/test_schema_gates.py`` compares the migrated schema against
``DatabaseModel.metadata`` and fails on any difference. Five columns carried
server-side defaults (or a nullability) that the ORM never declared and never
relied on — every writer goes through the ORM, which supplies its own Python
defaults (``uuid7()`` ids, ``datetime.now(UTC)`` timestamps, ``{}`` for
``extra_data``):

- ``oauth_tokens.created_at`` / ``updated_at``: ``DEFAULT now()`` (migration 005)
- ``oauth_tokens.extra_data``: ``DEFAULT '{}'``, nullable (migration 005) — the ORM
  declares it NOT NULL; no ORM write can produce a NULL, so the backfill below is
  a guard, not a data migration
- ``oauth_states.created_at``: ``DEFAULT now()`` (migration 010)
- ``playlist_sync_bases.id``: ``DEFAULT gen_random_uuid()`` (migration 030)

Dropping a default is a catalog-only change; ``SET NOT NULL`` scans
``oauth_tokens``, which holds one row per user per service.

Revision ID: 055_orm_schema_alignment
Revises: 054_vocabulary_check_constraints
Create Date: 2026-09-17
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "055_orm_schema_alignment"
down_revision: str | None = "054_vocabulary_check_constraints"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("oauth_tokens", "created_at", server_default=None)
    op.alter_column("oauth_tokens", "updated_at", server_default=None)
    op.execute(
        "UPDATE oauth_tokens SET extra_data = '{}'::jsonb WHERE extra_data IS NULL"
    )
    op.alter_column("oauth_tokens", "extra_data", server_default=None, nullable=False)
    op.alter_column("oauth_states", "created_at", server_default=None)
    op.alter_column("playlist_sync_bases", "id", server_default=None)


def downgrade() -> None:
    op.alter_column(
        "playlist_sync_bases", "id", server_default=sa.text("gen_random_uuid()")
    )
    op.alter_column("oauth_states", "created_at", server_default=sa.text("now()"))
    op.alter_column(
        "oauth_tokens",
        "extra_data",
        server_default=sa.text("'{}'::jsonb"),
        nullable=True,
    )
    op.alter_column("oauth_tokens", "updated_at", server_default=sa.text("now()"))
    op.alter_column("oauth_tokens", "created_at", server_default=sa.text("now()"))
