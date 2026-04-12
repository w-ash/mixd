"""Add track_preferences and track_preference_events tables.

The preference system (v0.7.0) lets users rate tracks as hmm/nah/yah/star.
Two tables: current state (track_preferences) and append-only event log
(track_preference_events) that preserves the full preference timeline.

Revision ID: 4f1c330aaa91
Revises: 6ebb3a9e7847
Create Date: 2026-04-11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "4f1c330aaa91"
down_revision: str | None = "6ebb3a9e7847"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ["track_preferences", "track_preference_events"]


def upgrade() -> None:
    # -- track_preferences: current preference state per user+track ----------
    op.create_table(
        "track_preferences",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.String(), server_default="default", nullable=False),
        sa.Column("track_id", sa.UUID(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("preferred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["track_id"],
            ["tracks.id"],
            name=op.f("fk_track_preferences_track_id_tracks"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_track_preferences")),
        sa.UniqueConstraint(
            "user_id",
            "track_id",
            name=op.f("uq_track_preferences_track_preferences_user_id"),
        ),
        sa.CheckConstraint(
            "state IN ('hmm', 'nah', 'yah', 'star')",
            name=op.f("ck_track_preferences_valid_state"),
        ),
    )
    op.create_index(
        "ix_track_preferences_user_id_state",
        "track_preferences",
        ["user_id", "state"],
    )
    op.create_index(
        "ix_track_preferences_user_id_preferred_at",
        "track_preferences",
        ["user_id", "preferred_at"],
    )

    # -- track_preference_events: append-only change log ---------------------
    op.create_table(
        "track_preference_events",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.String(), server_default="default", nullable=False),
        sa.Column("track_id", sa.UUID(), nullable=False),
        sa.Column("old_state", sa.String(length=16), nullable=True),
        sa.Column("new_state", sa.String(length=16), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("preferred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["track_id"],
            ["tracks.id"],
            name=op.f("fk_track_preference_events_track_id_tracks"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_track_preference_events")),
    )
    op.create_index(
        "ix_track_preference_events_user_id_track_id",
        "track_preference_events",
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
        "ix_track_preference_events_user_id_track_id",
        table_name="track_preference_events",
    )
    op.drop_table("track_preference_events")

    op.drop_index(
        "ix_track_preferences_user_id_state",
        table_name="track_preferences",
    )
    op.drop_index(
        "ix_track_preferences_user_id_preferred_at",
        table_name="track_preferences",
    )
    op.drop_table("track_preferences")
