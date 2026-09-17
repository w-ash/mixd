"""``resolution_events.entity_kind``: which typed mapping table a decision is about.

Tracks, artists and albums each keep their own mapping table, and every one
of them records its decisions in this one FK-free log. The ids in a row are
references by value, so the existing ``track_id`` / ``connector_track_id``
columns carry an artist's or album's ids unchanged — this column says which
table they name.

Added with a ``'track'`` default so every existing row is backfilled in the
same statement, then the default is dropped: every new row has to say what it
is about, and a writer that forgets fails with ``NotNullViolation`` rather than
filing an artist decision under tracks.

The vocabulary is inlined rather than imported from the domain: a migration is
a frozen record of the schema at this revision.
``tests/unit/migrations/test_058_resolution_event_entity_kind.py`` asserts the
list still equals the domain ``EntityKind`` — growing it means a new migration
that recreates the constraint.

Plain DDL, no ``CONCURRENTLY``/``NOT VALID``: the release command runs over the
Neon pooler URL, where ``autocommit_block`` cannot run (see 049 and 051).

Revision ID: 058_resolution_event_entity_kind
Revises: 057_likes_presence_rows
Create Date: 2026-09-17
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "058_resolution_event_entity_kind"
down_revision: str | None = "057_likes_presence_rows"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Sorted, so the rendered DDL matches the ORM's constraint text exactly.
ENTITY_KINDS: tuple[str, ...] = ("album", "artist", "track")

# Fully qualified (the ``ck_%(table_name)s_%(constraint_name)s`` convention
# already applied), passed through ``op.f`` so Alembic does not apply it a
# second time — the 054 pattern.
_CK_ENTITY_KIND = "ck_resolution_events_entity_kind_vocabulary"


def _in_list(column_name: str, vocabulary: tuple[str, ...]) -> str:
    members = ", ".join(f"'{value}'" for value in vocabulary)
    return f"{column_name} IN ({members})"


def upgrade() -> None:
    op.add_column(
        "resolution_events",
        sa.Column(
            "entity_kind",
            sa.String(length=16),
            nullable=False,
            server_default="track",
        ),
    )
    op.alter_column("resolution_events", "entity_kind", server_default=None)
    op.create_check_constraint(
        op.f(_CK_ENTITY_KIND),
        "resolution_events",
        _in_list("entity_kind", ENTITY_KINDS),
    )


def downgrade() -> None:
    op.drop_constraint(op.f(_CK_ENTITY_KIND), "resolution_events", type_="check")
    op.drop_column("resolution_events", "entity_kind")
