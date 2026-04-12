"""Add track_tags and track_tag_events tables.

The tagging system (v0.7.2) lets users categorize tracks by what they ARE
(mood, energy, context). Two tables mirror the preference schema: current
state (track_tags) and append-only event log (track_tag_events). Unique
key is three-part (user_id, track_id, tag) — a track can carry many tags.

GIN trigram index on ``tag`` supports autocomplete. The ``pg_trgm``
extension is already provisioned by migration 002_pg_opt, so only the
index is created here.

Revision ID: c602c5a08631
Revises: 4f1c330aaa91
Create Date: 2026-04-12
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c602c5a08631"
down_revision: str | None = "4f1c330aaa91"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ["track_tags", "track_tag_events"]


def upgrade() -> None:
    # -- track_tags: current tags per user+track -----------------------------
    op.create_table(
        "track_tags",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.String(), server_default="default", nullable=False),
        sa.Column("track_id", sa.UUID(), nullable=False),
        sa.Column("tag", sa.String(length=64), nullable=False),
        sa.Column("namespace", sa.String(length=32), nullable=True),
        sa.Column("value", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("tagged_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["track_id"],
            ["tracks.id"],
            name=op.f("fk_track_tags_track_id_tracks"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_track_tags")),
        sa.UniqueConstraint(
            "user_id",
            "track_id",
            "tag",
            name=op.f("uq_track_tags_user_id_track_id_tag"),
        ),
    )
    op.create_index(
        "ix_track_tags_user_id_tag",
        "track_tags",
        ["user_id", "tag"],
    )
    op.create_index(
        "ix_track_tags_user_id_namespace",
        "track_tags",
        ["user_id", "namespace"],
    )
    op.create_index(
        "ix_track_tags_user_id_tagged_at",
        "track_tags",
        ["user_id", "tagged_at"],
    )
    op.create_index(
        "ix_track_tags_tag_trgm",
        "track_tags",
        ["tag"],
        postgresql_using="gin",
        postgresql_ops={"tag": "gin_trgm_ops"},
    )

    # -- track_tag_events: append-only add/remove log ------------------------
    op.create_table(
        "track_tag_events",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.String(), server_default="default", nullable=False),
        sa.Column("track_id", sa.UUID(), nullable=False),
        sa.Column("tag", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=8), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("tagged_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["track_id"],
            ["tracks.id"],
            name=op.f("fk_track_tag_events_track_id_tracks"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_track_tag_events")),
        sa.CheckConstraint(
            "action IN ('add', 'remove')",
            name=op.f("ck_track_tag_events_valid_action"),
        ),
    )
    op.create_index(
        "ix_track_tag_events_user_id_track_id",
        "track_tag_events",
        ["user_id", "track_id"],
    )

    # -- RLS policies --------------------------------------------------------
    for table in _TABLES:
        op.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
        op.execute(
            sa.text(
                f"CREATE POLICY user_isolation ON {table} FOR ALL "
                f"USING (user_id = current_setting('app.user_id', TRUE))"
            )
        )


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.execute(sa.text(f"DROP POLICY IF EXISTS user_isolation ON {table}"))
        op.execute(sa.text(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY"))

    op.drop_index(
        "ix_track_tag_events_user_id_track_id",
        table_name="track_tag_events",
    )
    op.drop_table("track_tag_events")

    op.drop_index("ix_track_tags_tag_trgm", table_name="track_tags")
    op.drop_index("ix_track_tags_user_id_tagged_at", table_name="track_tags")
    op.drop_index("ix_track_tags_user_id_namespace", table_name="track_tags")
    op.drop_index("ix_track_tags_user_id_tag", table_name="track_tags")
    op.drop_table("track_tags")
