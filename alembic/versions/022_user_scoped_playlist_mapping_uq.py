"""User-scope the playlist_mappings uniqueness constraint.

The original ``uq_connector_playlist`` constraint enforced one canonical
playlist per external playlist *globally* — no user scope. With multi-user
mixd (April 2026 onward), two users importing the same Spotify playlist
URL would collide on this constraint. The v0.7.8.11/12 fix worked around
the crash by adding a probe and ``ON CONFLICT DO NOTHING``, but the probe
had to drop its user filter to mirror the constraint's perspective —
which means User B's save can route into User A's playlist row and
silently overwrite User A's data inside a single transaction.

The fix: rewrite the constraint to be ``(user_id, connector_playlist_id)``
so each user owns their own canonical record for a shared external
playlist. The migration also backfills ``playlist_mappings.user_id`` from
the parent ``playlists.user_id`` — historically the column relied on
``server_default = 'default'`` because ``_create_connector_mappings`` never
propagated the playlist owner.

Orphan mapping rows (whose ``playlist_id`` has no matching ``playlists``
row) are left at their existing ``user_id``; they self-heal on the next
save per v0.7.8.12's existing behavior.

Downgrade is best-effort: if any two rows now share a
``connector_playlist_id`` across users (which the new constraint allows
and the old constraint forbids), the rebuild fails. That's expected —
once two users own the same external playlist, you can't go back to a
single-user world without data loss.

Revision ID: 022_user_scoped_playlist_mapping_uq
Revises: 021_workflow_run_operation_id
Create Date: 2026-05-10
"""

from collections.abc import Sequence

from alembic import op

revision: str = "022_user_scoped_mapping_uq"
down_revision: str | None = "021_workflow_run_operation_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        UPDATE playlist_mappings AS pm
        SET user_id = p.user_id
        FROM playlists AS p
        WHERE pm.playlist_id = p.id
          AND pm.user_id IS DISTINCT FROM p.user_id
    """)

    op.drop_constraint(
        "uq_connector_playlist",
        "playlist_mappings",
        type_="unique",
    )

    op.create_unique_constraint(
        "uq_user_connector_playlist",
        "playlist_mappings",
        ["user_id", "connector_playlist_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_user_connector_playlist",
        "playlist_mappings",
        type_="unique",
    )

    op.create_unique_constraint(
        "uq_connector_playlist",
        "playlist_mappings",
        ["connector_playlist_id"],
    )
