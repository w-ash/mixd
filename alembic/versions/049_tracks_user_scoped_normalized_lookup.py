"""Scope the ``tracks`` normalized-lookup indexes to ``user_id``.

``find_tracks_by_title_artist`` is the only reader of ``title_normalized``,
``title_stripped`` and ``artist_normalized``, and every one of its reads is
user-scoped twice over: an explicit ``user_id`` predicate plus the RLS qual.
Neither existing index led with ``user_id`` and ``tracks`` has no standalone
``user_id`` index, so the probe could not get a user-scoped index prefix and
its cost grew with the whole table rather than with one user's library — the
within-run resolution decay the v0.10.2.11 audit measured.

Two in, two out: net write amplification on ``tracks`` — a hot insert path
during exactly the import this serves — is unchanged.

Plain DDL rather than CONCURRENTLY. ``CREATE INDEX`` takes ``SHARE`` (blocks
writes, allows reads) for well under a second at current size, and the release
command runs behind the v0.10.2.8 pre-deploy busy gate. When ``tracks`` grows
enough to matter, the escape hatch is ``op.get_context().autocommit_block()``
with ``postgresql_concurrently=True`` (precedent: 040, 044) — which also needs
a non-pooler connection, since PgBouncer's transaction mode cannot host it.

Revision ID: 049_tracks_user_normalized
Revises: 048_operation_runs_running_idx
Create Date: 2026-08-09
"""

from collections.abc import Sequence

from alembic import op

revision: str = "049_tracks_user_normalized"
down_revision: str | None = "048_operation_runs_running_idx"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "tracks"

_USER_SCOPED = (
    (
        "ix_tracks_user_normalized_lookup",
        ["user_id", "title_normalized", "artist_normalized"],
    ),
    (
        "ix_tracks_user_stripped_lookup",
        ["user_id", "title_stripped", "artist_normalized"],
    ),
)

_UNSCOPED = (
    ("ix_tracks_normalized_lookup", ["title_normalized", "artist_normalized"]),
    ("ix_tracks_stripped_lookup", ["title_stripped", "artist_normalized"]),
)


def upgrade() -> None:
    for name, columns in _USER_SCOPED:
        op.create_index(name, _TABLE, columns)
    for name, _ in _UNSCOPED:
        op.drop_index(name, table_name=_TABLE)


def downgrade() -> None:
    for name, columns in _UNSCOPED:
        op.create_index(name, _TABLE, columns)
    for name, _ in _USER_SCOPED:
        op.drop_index(name, table_name=_TABLE)
