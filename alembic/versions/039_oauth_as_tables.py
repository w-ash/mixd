"""Add the in-app OAuth 2.1 authorization-server tables (v0.9.5).

Four system tables backing the minimal AS that authorizes external MCP
clients against `https://mixd.me/mcp`:

- ``oauth_clients`` — DCR registrations plus the CIMD metadata cache, keyed
  by ``client_id`` (a UUID for DCR, the https metadata URL for CIMD).
- ``oauth_authorization_requests`` — authorization requests parked while the
  user completes the consent step in the web app (short TTL).
- ``oauth_authorization_codes`` — issued codes, stored **hashed**, single-use
  via atomic delete-returning at exchange time.
- ``oauth_refresh_tokens`` — rotating refresh tokens, stored **hashed**, with
  a ``family_id`` so a replayed (already-rotated) token revokes its whole
  family, and ``revoked_at`` marking rotated-out generations.

Postgres-backed (not in-memory) because ``auto_start_machines`` can land the
/authorize and /token calls on different Fly machines — the same reasoning as
the ``pending_actions`` store (migration 038).

NO RLS policy on any of these — deliberately: the ``/token`` endpoint runs
with **no authenticated user context** (the caller is an OAuth client, not a
session user), so ``current_setting('app.user_id')`` would hide every row.
These are AS machinery tables reachable only through the OAuth provider's
storage helpers; the user-facing consent API resolves rows by unguessable
primary keys. Precedent: ``chat_feedback`` (036) / ``schedules`` (025).

Revision ID: 039_oauth_as_tables
Revises: 038_pending_actions
Create Date: 2026-07-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "039_oauth_as_tables"
down_revision: str | None = "038_pending_actions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "oauth_clients",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("client_info", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_oauth_clients")),
        sa.UniqueConstraint("client_id", name=op.f("uq_oauth_clients_client_id")),
        sa.CheckConstraint(
            "kind IN ('dcr', 'cimd')", name=op.f("ck_oauth_clients_kind")
        ),
    )
    op.create_table(
        "oauth_authorization_requests",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.String(), nullable=False),
        sa.Column("client_name", sa.String(), nullable=True),
        sa.Column("params", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_oauth_authorization_requests")),
    )
    op.create_table(
        "oauth_authorization_codes",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("code_hash", sa.String(), nullable=False),
        sa.Column("client_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("scopes", sa.String(), nullable=False),
        sa.Column("code_challenge", sa.String(), nullable=False),
        sa.Column("redirect_uri", sa.String(), nullable=False),
        sa.Column("redirect_uri_provided_explicitly", sa.Boolean(), nullable=False),
        sa.Column("resource", sa.String(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_oauth_authorization_codes")),
        sa.UniqueConstraint(
            "code_hash", name=op.f("uq_oauth_authorization_codes_code_hash")
        ),
    )
    op.create_table(
        "oauth_refresh_tokens",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("family_id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("scopes", sa.String(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_oauth_refresh_tokens")),
        sa.UniqueConstraint(
            "token_hash", name=op.f("uq_oauth_refresh_tokens_token_hash")
        ),
    )
    op.create_index(
        "ix_oauth_refresh_tokens_family_id", "oauth_refresh_tokens", ["family_id"]
    )


def downgrade() -> None:
    op.drop_index(
        "ix_oauth_refresh_tokens_family_id", table_name="oauth_refresh_tokens"
    )
    op.drop_table("oauth_refresh_tokens")
    op.drop_table("oauth_authorization_codes")
    op.drop_table("oauth_authorization_requests")
    op.drop_table("oauth_clients")
