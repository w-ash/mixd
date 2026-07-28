"""Resolution event log + negative cache (v0.10.2).

Two tables, deliberately different in kind:

``resolution_events`` is the **append-only identity ledger** — one immutable row
per accept/reject/supersede/substitute decision, carrying the matcher version
that made it. It has **no foreign keys in either direction**, on purpose: an
immutable log must survive the deletion of anything it describes (a mapping
retired, a connector track cascaded away) or the audit trail would delete
itself exactly when it is most wanted, and a table free of inbound/outbound RI
is the one that can be range-partitioned later without dropping constraints
first. The ids it stores are therefore *references by value*, not enforced
edges; a dangling one reads as "the thing this decision was about is gone",
which is itself a true statement about history.

``resolution_negatives`` is **mutable state**, not history: retry counters and
cannot-link constraints that are updated in place and cascade-deleted with the
rows they are about. Two mechanisms share it, keyed by ``kind`` — ``no_match``
(no candidate; a backoff clock in ``check_again``) and ``rejected_pair`` (a
specific candidate, sticky until the matcher version or the content digest
changes). They must never collide on a key, so uniqueness is expressed as two
*partial* unique indexes rather than one nullable-column constraint: a single
``UNIQUE (user_id, connector_track_id, candidate_track_id)`` would let an
unbounded number of ``no_match`` rows coexist (NULLs are distinct by default),
and ``NULLS NOT DISTINCT`` would then force the two kinds to share one key
space.

Indexes on ``resolution_events`` are **btree, not BRIN** (memo §10.7, a
reversal of the earlier sketch): every query is tenant-scoped and returns few
rows, which favours btree by ~20x below ~10^6 rows, and uuid7 arrival order
interleaves tenants — so the physical clustering BRIN needs never exists.

Revision ID: 045_resolution_events
Revises: 044_track_mappings_supersession
Create Date: 2026-07-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "045_resolution_events"
down_revision: str | None = "044_track_mappings_supersession"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_EVENTS = "resolution_events"
_NEGATIVES = "resolution_negatives"

_EVENT_INDEXES = (
    "ix_resolution_events_user_time",
    "ix_resolution_events_connector_track",
    "ix_resolution_events_matcher_version",
)
_NEGATIVE_INDEXES = (
    "uq_resolution_negatives_no_match",
    "uq_resolution_negatives_pair",
    "ix_resolution_negatives_connector_track",
    "ix_resolution_negatives_candidate_track",
)
_KIND_CANDIDATE_CHECK = "ck_resolution_negatives_kind_candidate"


def _enable_rls(table: str) -> None:
    op.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
    op.execute(
        sa.text(
            f"CREATE POLICY user_isolation ON {table} FOR ALL "
            f"USING (user_id = current_setting('app.user_id', TRUE))"
        )
    )


def _disable_rls(table: str) -> None:
    op.execute(sa.text(f"DROP POLICY IF EXISTS user_isolation ON {table}"))
    op.execute(sa.text(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY"))


def _create_events() -> None:
    op.create_table(
        _EVENTS,
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.String(), server_default="default", nullable=False),
        # DB clock, never application-set: the one instant guaranteed monotone
        # and independent of whatever the writer believed the time was.
        # ``clock_timestamp()``, not ``now()``: ``now()`` is the *transaction*
        # start, so every event a single transaction writes would share one
        # instant and the log would lose the order of decisions inside it —
        # exactly the ordering "why does my library believe this" reads.
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        # Matcher clock (equal to recorded_at online, earlier on a backfill or
        # offline re-resolution) and provider-snapshot freshness. Both nullable:
        # not every event type has a meaningful decision or evidence instant.
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("evidence_as_of", sa.DateTime(timezone=True), nullable=True),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("matcher_version", sa.String(32), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("connector_name", sa.String(32), nullable=True),
        sa.Column("connector_track_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("track_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("resulting_mapping_id", postgresql.UUID(as_uuid=True), nullable=True),
        # Calibration-ready from birth: score/zone/selection_probability are
        # unrecoverable after the fact, so they are recorded at decision time
        # even though calibration itself is out of scope (memo §10.4).
        sa.Column("confidence", sa.Integer(), nullable=True),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("zone", sa.String(16), nullable=True),
        sa.Column("selection_probability", sa.Float(), nullable=True),
        sa.Column(
            "payload",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_resolution_events")),
        # No ForeignKeyConstraint anywhere — see the module docstring.
    )
    # The tenant timeline query ("what happened to my library, newest first").
    op.create_index(
        _EVENT_INDEXES[0],
        _EVENTS,
        ["user_id", sa.text("recorded_at DESC")],
    )
    # "Why does my library believe this?" — partial because the majority of
    # rows for some event types carry no connector track at all.
    op.create_index(
        _EVENT_INDEXES[1],
        _EVENTS,
        ["user_id", "connector_track_id"],
        postgresql_where=sa.text("connector_track_id IS NOT NULL"),
    )
    # Cross-tenant by design: "which decisions did matcher X make, and when" is
    # a calibration/drift question about the matcher, not about one user.
    op.create_index(
        _EVENT_INDEXES[2],
        _EVENTS,
        ["matcher_version", sa.text("recorded_at DESC")],
    )
    _enable_rls(_EVENTS)


def _create_negatives() -> None:
    op.create_table(
        _NEGATIVES,
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.String(), server_default="default", nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("connector_name", sa.String(32), nullable=False),
        sa.Column("connector_track_id", postgresql.UUID(as_uuid=True), nullable=False),
        # NULL for `no_match`: the search came up empty, so there is no
        # candidate to name.
        sa.Column("candidate_track_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("matcher_version", sa.String(32), nullable=False),
        sa.Column("content_digest", sa.String(64), nullable=True),
        sa.Column(
            "consecutive_misses", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column("check_again", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("unrejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_resolution_negatives")),
        # The two kinds share a table but not a shape, and the partial unique
        # indexes below only *assume* the split: a `no_match` row that acquired
        # a candidate would silently escape both key spaces and become an
        # unbounded duplicate. The CHECK is what makes the assumption true.
        sa.CheckConstraint(
            "(kind = 'no_match' AND candidate_track_id IS NULL) "
            "OR (kind = 'rejected_pair' AND candidate_track_id IS NOT NULL)",
            name=_KIND_CANDIDATE_CHECK,
        ),
        # Unlike the event log, these rows are state about live entities: when
        # the entity goes, so does the state.
        sa.ForeignKeyConstraint(
            ["connector_track_id"],
            ["connector_tracks.id"],
            name=op.f("fk_resolution_negatives_connector_track_id_connector_tracks"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_track_id"],
            ["tracks.id"],
            name=op.f("fk_resolution_negatives_candidate_track_id_tracks"),
            ondelete="CASCADE",
        ),
    )
    # One backoff state per (user, connector track, connector) …
    op.create_index(
        _NEGATIVE_INDEXES[0],
        _NEGATIVES,
        ["user_id", "connector_track_id", "connector_name"],
        unique=True,
        postgresql_where=sa.text("kind = 'no_match'"),
    )
    # … and one cannot-link row per candidate pair, in a key space that cannot
    # collide with the one above.
    op.create_index(
        _NEGATIVE_INDEXES[1],
        _NEGATIVES,
        ["user_id", "connector_track_id", "candidate_track_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'rejected_pair'"),
    )
    # Both CASCADE FKs need their own index: the unique indexes lead with
    # user_id (and are partial), so neither can serve the delete-time probe.
    op.create_index(_NEGATIVE_INDEXES[2], _NEGATIVES, ["connector_track_id"])
    op.create_index(_NEGATIVE_INDEXES[3], _NEGATIVES, ["candidate_track_id"])
    _enable_rls(_NEGATIVES)


def upgrade() -> None:
    _create_events()
    _create_negatives()


def downgrade() -> None:
    for table, indexes in ((_NEGATIVES, _NEGATIVE_INDEXES), (_EVENTS, _EVENT_INDEXES)):
        _disable_rls(table)
        for index in reversed(indexes):
            op.drop_index(index, table_name=table)
        op.drop_table(table)
