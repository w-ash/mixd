"""Listening history: canonical plays, raw connector plays, and the provenance edge."""

from datetime import datetime

from sqlalchemy import (
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
from src.infrastructure.persistence.database.models.base import (
    BaseEntity,
    PgArray,
    PgJsonb,
    PgUuidCol,
    UuidType,
)
from src.infrastructure.persistence.database.models.track import DBTrack


class DBTrackPlay(BaseEntity):
    """Immutable record of track plays across services.

    Represents user listening history across music services for analytics and insights.
    """

    __tablename__: str = "track_plays"
    __table_args__: tuple[SchemaItem, ...] = (
        # Unique constraint to prevent duplicate plays (safety net for application-level deduplication)
        UniqueConstraint(
            "user_id",
            "track_id",
            "service",
            "played_at",
            "ms_played",
            name="uq_track_plays_deduplication",
            # NULL ms_played rows (all Last.fm scrobbles) must still collide —
            # without this, ON CONFLICT never fires for them (migration 040).
            postgresql_nulls_not_distinct=True,
        ),
        # Existing indexes
        Index("ix_track_plays_service", "service"),
        Index("ix_track_plays_played_at", "played_at"),
        Index("ix_track_plays_import_source", "import_source"),
        Index("ix_track_plays_import_batch", "import_batch_id"),
        # Critical performance indexes for play history queries
        Index("ix_track_plays_track_id", "track_id"),  # Per-track queries
        Index(
            "ix_track_plays_track_played", "track_id", "played_at"
        ),  # Time-range filtering
        Index(
            "ix_track_plays_track_service", "track_id", "service"
        ),  # Service-specific queries
        # NOTE: BRIN index on played_at created via Alembic migration 002_pg_opt
    )

    user_id: Mapped[str] = mapped_column(String(), nullable=False)
    track_id: Mapped[UuidType] = mapped_column(
        PgUuidCol(as_uuid=True), ForeignKey("tracks.id", ondelete="CASCADE")
    )
    service: Mapped[str] = mapped_column(String(32))  # 'spotify', 'lastfm', 'mixd'
    played_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    ms_played: Mapped[int | None]
    context: Mapped[JsonDict | None] = mapped_column(PgJsonb)

    # Cross-source deduplication: which services contributed to this play record
    source_services: Mapped[list[str] | None] = mapped_column(
        PgArray(String()), nullable=True
    )

    # Import tracking (service-agnostic)
    import_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    import_source: Mapped[str | None] = mapped_column(
        String(32)
    )  # 'spotify_export', 'lastfm_api', 'manual'
    import_batch_id: Mapped[str | None] = mapped_column(String())

    # Relationships
    track: Mapped[DBTrack] = relationship(
        back_populates="plays",
        passive_deletes=True,
        lazy="raise_on_sql",
    )


class DBConnectorPlay(BaseEntity):
    """Raw play data from external music services before resolution.

    Stores play events from external APIs (Spotify, Last.fm) with complete metadata
    for eventual resolution to canonical tracks. Follows the same pattern as
    DBConnectorTrack for separation of ingestion and resolution concerns.
    """

    __tablename__: str = "connector_plays"

    user_id: Mapped[str] = mapped_column(String(), nullable=False)
    connector_name: Mapped[str] = mapped_column(String(32))  # "lastfm", "spotify"
    connector_track_identifier: Mapped[str] = mapped_column(
        String()
    )  # "artist::title" for lastfm

    # Play event data
    played_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    ms_played: Mapped[int | None]

    # Raw API data preservation
    raw_metadata: Mapped[JsonDict] = mapped_column(PgJsonb)

    # Resolution tracking (nullable until resolved)
    resolved_track_id: Mapped[UuidType | None] = mapped_column(
        PgUuidCol(as_uuid=True),
        ForeignKey("tracks.id", ondelete="CASCADE"),
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )
    # Why a row with no resolved_track_id is absent from canonical history.
    # Explanatory only — resolved_track_id remains the projection's predicate,
    # because an unprocessed row is also reason-less and must stay out. The
    # export is an event log, so most excluded rows are skips whose track
    # resolved perfectly well; without this column that is indistinguishable
    # from a resolution failure. See PlayExclusionReason for the vocabulary.
    exclusion_reason: Mapped[str | None] = mapped_column(String(16))

    # Import tracking
    import_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    import_source: Mapped[str | None] = mapped_column(
        String(32)
    )  # "lastfm_api", "spotify_export"
    import_batch_id: Mapped[str | None] = mapped_column(String())

    # Relationships (only to resolved track, if any)
    resolved_track: Mapped[DBTrack | None] = relationship(
        back_populates="connector_plays",
        passive_deletes=True,
        lazy="raise_on_sql",
    )

    __table_args__: tuple[SchemaItem, ...] = (
        # Prevent duplicate connector plays (same as track_plays deduplication pattern)
        UniqueConstraint(
            "user_id",
            "connector_name",
            "connector_track_identifier",
            "played_at",
            "ms_played",
            name="uq_connector_plays_deduplication",
            # NULL ms_played rows (all Last.fm scrobbles) must still collide —
            # without this, ON CONFLICT never fires for them (migration 040).
            postgresql_nulls_not_distinct=True,
        ),
        # Performance indexes for common queries
        Index("ix_connector_plays_connector", "connector_name"),
        Index("ix_connector_plays_played_at", "played_at"),
        Index("ix_connector_plays_resolved_track", "resolved_track_id"),
        Index(
            "ix_connector_plays_unresolved",
            "connector_name",
            "resolved_track_id",
            postgresql_where=text("resolved_track_id IS NULL"),
        ),  # Partial: only unresolved rows — the query this index exists for
        Index("ix_connector_plays_import_batch", "import_batch_id"),
    )


class DBPlaySource(BaseEntity):
    """Membership edge: which ledger observation backs which canonical play.

    Materialized so projection idempotence is a mechanical no-op diff and
    provenance survives without parsing context. One row per observation —
    an observation contributes to exactly one canonical play (v0.10.0).
    """

    __tablename__: str = "play_sources"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint(
            "user_id",
            "connector_play_id",
            name="uq_play_sources_connector_play",
        ),
        Index("ix_play_sources_track_play", "track_play_id"),
        # Supports the connector_play_id CASCADE FK probe — the unique
        # constraint leads with user_id and cannot serve it.
        Index("ix_play_sources_connector_play", "connector_play_id"),
    )

    user_id: Mapped[str] = mapped_column(String(), nullable=False)
    track_play_id: Mapped[UuidType] = mapped_column(
        PgUuidCol(as_uuid=True),
        ForeignKey("track_plays.id", ondelete="CASCADE"),
        nullable=False,
    )
    connector_play_id: Mapped[UuidType] = mapped_column(
        PgUuidCol(as_uuid=True),
        ForeignKey("connector_plays.id", ondelete="CASCADE"),
        nullable=False,
    )
