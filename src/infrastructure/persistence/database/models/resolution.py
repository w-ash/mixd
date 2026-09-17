"""The identity-resolution event log and the remembered non-match cache (v0.10.2)."""

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.schema import SchemaItem

from src.domain.entities.shared import JsonDict
from src.infrastructure.persistence.database.models.base import (
    BaseEntity,
    DatabaseModel,
    PgJsonb,
    PgUuidCol,
    UuidType,
)


class DBResolutionEvent(DatabaseModel):
    """One immutable identity-resolution decision (v0.10.2, migration 045).

    Deliberately NOT a ``BaseEntity``: ``created_at``/``updated_at`` are
    meaningless on a row that is written once and never touched again — its
    time is ``recorded_at`` (the DB clock), ``decided_at`` (the matcher clock),
    and ``evidence_as_of`` (provider-snapshot freshness). Collapsing those into
    one instant is the failure the split exists to prevent: a bulk
    re-resolution would stamp its own wall clock over history and "what did we
    believe on date X" would be gone for good.

    **No foreign keys, in or out.** The ids here are references by value: the
    log has to outlive the mapping, track, or connector track it describes, and
    a table free of RI is the one that can be partitioned later without
    dropping constraints first (memo §10.7).

    MUST mirror migrations 045 + 046 exactly — integration tests build the
    schema with ``metadata.create_all``, so divergence means every integration
    test runs against a schema production never has.
    """

    __tablename__: str = "resolution_events"

    user_id: Mapped[str] = mapped_column(String(), nullable=False)
    # DB-assigned and never set from Python — the writer's clock is not a
    # trustworthy ordering key across processes. ``clock_timestamp()`` rather
    # than ``now()``: the latter is the transaction's start instant, so every
    # event one transaction writes would tie and the log would lose the order
    # of decisions within it.
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("clock_timestamp()"),
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    evidence_as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    matcher_version: Mapped[str] = mapped_column(String(32), nullable=False)
    run_id: Mapped[UuidType | None] = mapped_column(PgUuidCol(as_uuid=True))
    connector_name: Mapped[str | None] = mapped_column(String(32))
    connector_track_id: Mapped[UuidType | None] = mapped_column(PgUuidCol(as_uuid=True))
    track_id: Mapped[UuidType | None] = mapped_column(PgUuidCol(as_uuid=True))
    resulting_mapping_id: Mapped[UuidType | None] = mapped_column(
        PgUuidCol(as_uuid=True)
    )
    # Recorded at decision time because none of them is recoverable afterwards:
    # future calibration needs the score, the zone it fell in, and the
    # probability the candidate was offered for review at all.
    confidence: Mapped[int | None]
    score: Mapped[float | None]
    zone: Mapped[str | None] = mapped_column(String(16))
    selection_probability: Mapped[float | None]
    payload: Mapped[JsonDict] = mapped_column(
        PgJsonb, nullable=False, server_default=text("'{}'::jsonb")
    )

    __table_args__: tuple[SchemaItem, ...] = (
        # Btree, not BRIN: tenant-scoped small-result queries, and uuid7
        # arrival interleaves tenants so BRIN's clustering premise never holds.
        Index("ix_resolution_events_user_time", "user_id", text("recorded_at DESC")),
        Index(
            "ix_resolution_events_connector_track",
            "user_id",
            "connector_track_id",
            postgresql_where=text("connector_track_id IS NOT NULL"),
        ),
        # The log's only read path (``events_for_mapping``): one mapping's
        # history, newest first. Partial because most events name no mapping,
        # and a NULL there can never satisfy the equality predicate anyway.
        Index(
            "ix_resolution_events_mapping",
            "user_id",
            "resulting_mapping_id",
            text("recorded_at DESC"),
            postgresql_where=text("resulting_mapping_id IS NOT NULL"),
        ),
    )


class DBResolutionNegative(BaseEntity):
    """Remembered non-match: a retry clock or a sticky cannot-link (v0.10.2).

    Mutable state rather than history, which is why (unlike the event log) both
    id columns are real cascading foreign keys — when the connector track or
    the candidate goes away, so does the constraint about it.

    Uniqueness is two partial indexes, not one constraint: ``no_match`` rows
    carry a NULL ``candidate_track_id``, and under default NULL semantics a
    single unique over the triple would admit unlimited duplicates of exactly
    the row that must be a singleton.
    """

    __tablename__: str = "resolution_negatives"

    user_id: Mapped[str] = mapped_column(String(), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    connector_name: Mapped[str] = mapped_column(String(32), nullable=False)
    connector_track_id: Mapped[UuidType] = mapped_column(
        PgUuidCol(as_uuid=True),
        ForeignKey("connector_tracks.id", ondelete="CASCADE"),
        nullable=False,
    )
    # NULL for `no_match` — an empty search names no candidate.
    candidate_track_id: Mapped[UuidType | None] = mapped_column(
        PgUuidCol(as_uuid=True), ForeignKey("tracks.id", ondelete="CASCADE")
    )
    matcher_version: Mapped[str] = mapped_column(String(32), nullable=False)
    # `rejected_pair` only: digest of both sides' match-relevant fields, so
    # editing either side expires the rejection instead of letting it outlive
    # the data it was computed from.
    content_digest: Mapped[str | None] = mapped_column(String(64))
    consecutive_misses: Mapped[int] = mapped_column(
        nullable=False, default=0, server_default="0"
    )
    check_again: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    unrejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__: tuple[SchemaItem, ...] = (
        # The partial indexes below only *assume* the kind/candidate split; a
        # `no_match` row that acquired a candidate would escape both key spaces
        # and duplicate without limit. This is what makes the assumption true.
        CheckConstraint(
            "(kind = 'no_match' AND candidate_track_id IS NULL) "
            "OR (kind = 'rejected_pair' AND candidate_track_id IS NOT NULL)",
            name="ck_resolution_negatives_kind_candidate",
        ),
        Index(
            "uq_resolution_negatives_no_match",
            "user_id",
            "connector_track_id",
            "connector_name",
            unique=True,
            postgresql_where=text("kind = 'no_match'"),
        ),
        # One row per (owner, connector track, candidate). Every reader of this
        # table honours every rejection in it, so there is nothing a second row
        # on the same pair could say that the first does not already say.
        Index(
            "uq_resolution_negatives_pair",
            "user_id",
            "connector_track_id",
            "candidate_track_id",
            unique=True,
            postgresql_where=text("kind = 'rejected_pair'"),
        ),
        # Both CASCADE FKs need their own index — the unique indexes above are
        # partial and lead with user_id, so neither serves the delete probe.
        Index("ix_resolution_negatives_connector_track", "connector_track_id"),
        Index("ix_resolution_negatives_candidate_track", "candidate_track_id"),
    )
