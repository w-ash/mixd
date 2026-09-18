"""Connector-track to canonical-track mappings and the human match-review queue."""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql.schema import SchemaItem

from src.domain.entities.shared import JsonDict
from src.domain.entities.track_mapping import MAPPING_ORIGINS, MATCH_METHODS
from src.infrastructure.persistence.database.models.base import (
    BaseEntity,
    PgJsonb,
    PgUuidCol,
    UuidType,
    vocabulary_check,
)
from src.infrastructure.persistence.database.models.track import (
    DBConnectorTrack,
    DBTrack,
)


class DBTrackMapping(BaseEntity):
    """Maps external connector tracks to internal canonical tracks.

    Represents the relationship between external API tracks and canonical tracks.
    Mappings can be recreated from connector data when needed.
    """

    __tablename__: str = "track_mappings"

    user_id: Mapped[str] = mapped_column(String(), nullable=False)
    # RESTRICT, not CASCADE (migration 051): mappings are append-only history
    # and a cascade is a silent history delete — production reached a state
    # where resolution_events named superseding mappings a track delete had
    # already taken. A caller that means to remove the track moves its
    # mappings first (``merge_mappings_to_track``); everything else must fail
    # at the database, because the repository guard in ``hard_delete_track``
    # cannot see a raw Core DELETE.
    track_id: Mapped[UuidType] = mapped_column(
        PgUuidCol(as_uuid=True), ForeignKey("tracks.id", ondelete="RESTRICT")
    )
    connector_track_id: Mapped[UuidType] = mapped_column(
        PgUuidCol(as_uuid=True),
        ForeignKey("connector_tracks.id", ondelete="RESTRICT"),
    )
    connector_name: Mapped[str] = mapped_column(String(32), nullable=False)
    match_method: Mapped[str] = mapped_column(String(32))
    confidence: Mapped[int]
    confidence_evidence: Mapped[JsonDict | None] = mapped_column(PgJsonb)
    origin: Mapped[str] = mapped_column(
        String(20), nullable=False, default="automatic", server_default="automatic"
    )
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Freshness signal: when an import last re-encountered this mapping's
    # connector track. Deliberately NOT evidence — re-encounter proves the
    # connector track exists, not that the canonical match is right (FM1a).
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Supersession (v0.10.2, migration 044) — mappings are append-only: a
    # changed re-assertion retires this row and inserts a successor rather
    # than overwriting in place.  DEFERRABLE INITIALLY DEFERRED because the
    # write path stamps the predecessor with an id the successor INSERT has
    # not written yet (legal alongside ON CONFLICT: the arbiter is the unique
    # index, and only arbiters must be non-deferrable).  ON DELETE RESTRICT
    # since 051: SET NULL let a deleted successor blank its predecessor's
    # pointer, leaving a retired row with a reason and no successor —
    # indistinguishable from a retirement that never had one. RESTRICT's
    # delete check is non-deferrable regardless of the clause below, which
    # only governs the insert-side check the write path defers.
    superseded_by_id: Mapped[UuidType | None] = mapped_column(
        PgUuidCol(as_uuid=True),
        ForeignKey(
            "track_mappings.id",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
    )
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    supersession_reason: Mapped[str | None] = mapped_column(String(32))
    # None = global. A non-None value scopes the retirement to one
    # market/storefront (contextual substitution never retires an incumbent).
    supersession_scope: Mapped[str | None] = mapped_column(String(64))
    # FM4a substrate: next scheduled re-verification of a live mapping. No
    # worker reads it yet — the column exists so one can be added without a
    # migration.
    next_verify_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Relationships
    track: Mapped[DBTrack] = relationship(
        back_populates="mappings",
        passive_deletes=True,
        lazy="raise_on_sql",
    )
    connector_track: Mapped[DBConnectorTrack] = relationship(
        back_populates="mappings",
        passive_deletes=True,
        lazy="raise_on_sql",
    )

    # MUST mirror migration 044 exactly: integration tests build the schema
    # with ``metadata.create_all``, not the migration chain, so any divergence
    # means every integration test runs against a schema production never has.
    __table_args__: tuple[SchemaItem, ...] = (
        # Live uniqueness (044 replaced the full unique constraint): superseded
        # rows are history and share the key with their successor.
        Index(
            "uq_track_mappings_live_connector",
            "user_id",
            "connector_track_id",
            "connector_name",
            unique=True,
            postgresql_where=text("superseded_at IS NULL"),
        ),
        # User-scoped partial unique: one live primary per user-track-connector triple
        Index(
            "uq_primary_mapping",
            "user_id",
            "track_id",
            "connector_name",
            unique=True,
            postgresql_where=text("is_primary = TRUE AND superseded_at IS NULL"),
        ),
        # The three supersession columns move as a unit; the successor pointer
        # is optional (retirement with no replacement).
        CheckConstraint(
            "(superseded_at IS NULL AND superseded_by_id IS NULL "
            "AND supersession_reason IS NULL) "
            "OR (superseded_at IS NOT NULL AND supersession_reason IS NOT NULL)",
            name="supersession_coherent",
        ),
        # Storage-boundary enforcement of the domain vocabularies (migration
        # 054). Growing either vocabulary means a new migration that drops and
        # recreates the constraint — the Literal alias alone does not migrate
        # the database.
        CheckConstraint(
            vocabulary_check("match_method", MATCH_METHODS),
            name="match_method_vocabulary",
        ),
        CheckConstraint(
            vocabulary_check("origin", MAPPING_ORIGINS),
            name="origin_vocabulary",
        ),
        # Performance indexes for common lookup patterns
        Index("ix_track_mappings_track_lookup", "track_id"),
        Index("ix_track_mappings_connector_lookup", "connector_track_id"),
        Index("ix_track_mappings_connector_name", "connector_name"),
        # Live reader index; and the chain walk, which only ever visits rows
        # that actually point at a successor.
        Index(
            "ix_track_mappings_live_track",
            "user_id",
            "track_id",
            postgresql_where=text("superseded_at IS NULL"),
        ),
        Index(
            "ix_track_mappings_superseded_by",
            "superseded_by_id",
            postgresql_where=text("superseded_by_id IS NOT NULL"),
        ),
    )


class DBMatchReview(BaseEntity):
    """Proposed track-to-connector match awaiting human review.

    Stores medium-confidence matches (between auto-reject and auto-accept
    thresholds) as a staging area separate from track_mappings. On accept,
    a real DBTrackMapping is created. On reject, the row is marked to
    prevent re-queuing the same pair.
    """

    __tablename__: str = "match_reviews"

    user_id: Mapped[str] = mapped_column(String(), nullable=False)
    track_id: Mapped[UuidType] = mapped_column(
        PgUuidCol(as_uuid=True), ForeignKey("tracks.id", ondelete="CASCADE")
    )
    connector_name: Mapped[str] = mapped_column(String(32), nullable=False)
    connector_track_id: Mapped[UuidType] = mapped_column(
        PgUuidCol(as_uuid=True),
        ForeignKey("connector_tracks.id", ondelete="CASCADE"),
    )
    match_method: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[int] = mapped_column(nullable=False)
    match_weight: Mapped[float] = mapped_column(nullable=False)
    confidence_evidence: Mapped[JsonDict | None] = mapped_column(PgJsonb)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default="pending"
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Relationships
    track: Mapped[DBTrack] = relationship(passive_deletes=True, lazy="raise_on_sql")
    connector_track: Mapped[DBConnectorTrack] = relationship(
        passive_deletes=True, lazy="raise_on_sql"
    )

    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint(
            "user_id",
            "track_id",
            "connector_name",
            "connector_track_id",
            name="uq_match_reviews_user_track_connector",
        ),
        # Same vocabulary as track_mappings: an accepted review becomes a
        # mapping carrying this method. Growing the vocabulary means a new
        # migration (054).
        CheckConstraint(
            vocabulary_check("match_method", MATCH_METHODS),
            name="match_method_vocabulary",
        ),
        Index("ix_match_reviews_status", "status"),
        Index("ix_match_reviews_track_id", "track_id"),
    )
