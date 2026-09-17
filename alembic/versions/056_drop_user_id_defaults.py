"""Drop the ``DEFAULT 'default'`` on every tenanted ``user_id`` column.

A column default supplies a tenant without anyone declaring one: an INSERT
that omits ``user_id`` lands in a phantom tenant instead of failing. Three
such tenants (17,723 rows) were purged from production on 2026-09-06. The
v0.10.4.1 session guard refuses a transaction that *declares* itself
``default`` against a hosted database; this migration closes the other half,
so the omitted column violates NOT NULL and the INSERT fails loudly.

The ORM columns lose ``default=``/``server_default=`` in the same release,
and the domain entities lose their ``"default"`` field default, so a tenant
must be named explicitly at every layer.

Plain DDL: the release command runs over the Neon pooler URL, where
``autocommit_block`` cannot run (see 049 and 051). ``DROP DEFAULT`` is a
catalog-only change and takes no table rewrite.

Revision ID: 056_drop_user_id_defaults
Revises: 055_orm_schema_alignment
Create Date: 2026-09-17
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "056_drop_user_id_defaults"
down_revision: str | None = "055_orm_schema_alignment"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Every table whose ORM model carried ``server_default="default"`` on
# ``user_id`` at this revision. Tables that derive their tenant through a
# CASCADE parent (playlist_tracks, workflow_*) never had the column default
# and are not listed; ``workflows.user_id`` is nullable with no default.
TENANTED_TABLES: tuple[str, ...] = (
    "connector_plays",
    "match_reviews",
    "oauth_tokens",
    "operation_runs",
    "play_sources",
    "playlist_assignment_members",
    "playlist_assignments",
    "playlist_mappings",
    "playlist_sync_bases",
    "playlists",
    "resolution_events",
    "resolution_negatives",
    "schedules",
    "track_likes",
    "track_mappings",
    "track_metrics",
    "track_plays",
    "track_preference_events",
    "track_preferences",
    "track_tag_events",
    "track_tags",
    "tracks",
    "user_settings",
)


def upgrade() -> None:
    for table in TENANTED_TABLES:
        op.alter_column(table, "user_id", server_default=None)


def downgrade() -> None:
    for table in TENANTED_TABLES:
        op.alter_column(table, "user_id", server_default="default")
