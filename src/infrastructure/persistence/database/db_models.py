"""SQLAlchemy database models for the Mixd music platform.

This module implements a simplified architecture using hard deletes for all entities,
following SQLAlchemy 2.0 best practices with modern type annotations.

Architecture:
- DatabaseModel: Single DeclarativeBase foundation with ``type_annotation_map``
  that resolves ``Mapped[JsonDict]`` to ``postgresql.JSONB`` automatically.
  This means JSONB columns can use the domain ``JsonDict`` alias instead of
  ``dict[str, Any]`` — the type flows all the way from Python to the column.
- TimestampMixin: Provides created_at/updated_at for audit trails

All entities use hard deletes for simplicity and performance.
Data recovery relies on external API re-import and database backups.
"""

from datetime import UTC, datetime
from typing import Any, ClassVar
import uuid as uuid_mod

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    MetaData,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects import postgresql as pg_dialect
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)
from sqlalchemy.sql.schema import SchemaItem

from src.config import get_logger
from src.domain.entities.shared import JsonDict

# Type aliases to avoid import name conflicts between stdlib uuid and SQLAlchemy UUID
UuidType = uuid_mod.UUID
PgUuidCol = pg_dialect.UUID
PgJsonb = pg_dialect.JSONB
PgArray = pg_dialect.ARRAY

logger = get_logger(__name__)

# Define naming convention for constraints (SQLAlchemy 2.0 best practice)
convention = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",  # Index
    "uq": "uq_%(table_name)s_%(column_0_label)s",  # Unique constraint
    "ck": "ck_%(table_name)s_%(constraint_name)s",  # Check constraint
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",  # Foreign key
    "pk": "pk_%(table_name)s",  # Primary key
}

# Create metadata with naming convention (single source of truth)
metadata = MetaData(naming_convention=convention)


class DatabaseModel(AsyncAttrs, DeclarativeBase):
    """Foundation model for all database entities.

    Single DeclarativeBase inheritance following SQLAlchemy 2.0 best practices.
    All database models inherit from this class to ensure consistent metadata handling.

    The ``type_annotation_map`` routes ``Mapped[JsonDict]`` to ``postgresql.JSONB``
    so JSONB columns get typed metadata without every ``mapped_column`` needing
    an explicit ``PgJsonb`` argument. SQLAlchemy 2.0.37+ matches union types by
    content (excluding ``None``), so ``Mapped[JsonDict | None]`` also resolves
    via this single entry. See: docs.sqlalchemy.org/en/20/orm/declarative_tables.
    """

    metadata: ClassVar[MetaData] = metadata

    # SQLAlchemy stubs declare type_annotation_map as dict[Any, Any]; the runtime
    # match is by key shape (JsonDict here), the value side typing is informational.
    type_annotation_map: ClassVar[dict[Any, Any]] = {  # pyright: ignore[reportExplicitAny]  # SQLAlchemy stub shape
        JsonDict: PgJsonb,
    }

    id: Mapped[UuidType] = mapped_column(
        PgUuidCol(as_uuid=True), primary_key=True, default=uuid_mod.uuid7, sort_order=-1
    )


class TimestampMixin:
    """Provides created_at/updated_at timestamps for all entities.

    Mixin pattern following SQLAlchemy 2.0 declarative mixins best practices.
    Applied to all entities for audit trail.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )


class BaseEntity(DatabaseModel, TimestampMixin):
    """Base class for all database entities.

    All entities inherit from this class for consistent timestamp behavior.
    Uses hard deletes for simplicity and performance.
    """

    __abstract__: ClassVar[bool] = True


class DBTrack(BaseEntity):
    """Core track entity with essential metadata.

    Represents the user's music library with plays, likes, and playlist associations.
    Uses hard deletes for simplicity and performance.
    """

    __tablename__: str = "tracks"

    user_id: Mapped[str] = mapped_column(
        String(), nullable=False, default="default", server_default="default"
    )
    version: Mapped[int] = mapped_column(default=1, server_default="1")
    title: Mapped[str] = mapped_column(String(), nullable=False)
    artists: Mapped[JsonDict] = mapped_column(PgJsonb, nullable=False)
    album: Mapped[str | None] = mapped_column(String())
    duration_ms: Mapped[int | None]
    release_date: Mapped[datetime | None]
    isrc: Mapped[str | None] = mapped_column(String(32), index=True)
    spotify_id: Mapped[str | None] = mapped_column(String(), index=True)
    mbid: Mapped[str | None] = mapped_column(String(36), index=True)

    # Pre-computed normalized text for fuzzy matching (diacritics stripped, lowercased, etc.)
    title_normalized: Mapped[str | None] = mapped_column(String())
    artist_normalized: Mapped[str | None] = mapped_column(String())
    # Normalized title with parentheticals stripped — enables matching
    # "Song (feat. X)" ↔ "Song" by comparing stripped forms
    title_stripped: Mapped[str | None] = mapped_column(String())
    # Denormalized artist text for search and sorting (e.g., "Artist1, Artist2")
    artists_text: Mapped[str | None] = mapped_column(String())

    # Relationships
    mappings: Mapped[list[DBTrackMapping]] = relationship(
        back_populates="track",
        passive_deletes=True,
    )
    metrics: Mapped[list[DBTrackMetric]] = relationship(
        back_populates="track",
        cascade="all, delete-orphan",
    )
    likes: Mapped[list[DBTrackLike]] = relationship(
        back_populates="track",
        cascade="all, delete-orphan",
    )
    plays: Mapped[list[DBTrackPlay]] = relationship(
        back_populates="track",
        cascade="all, delete-orphan",
    )
    playlist_tracks: Mapped[list[DBPlaylistTrack]] = relationship(
        back_populates="track",
        cascade="all, delete-orphan",
    )
    connector_plays: Mapped[list[DBConnectorPlay]] = relationship(
        back_populates="resolved_track",
        passive_deletes=True,
    )
    preferences: Mapped[list[DBTrackPreference]] = relationship(
        back_populates="track",
        cascade="all, delete-orphan",
    )

    # NOTE: pg_trgm GIN indexes (title, album, artists_text) and the JSONB GIN
    # index on artists are created only via Alembic migration 002_pg_opt — they
    # require the pg_trgm extension and would fail with metadata.create_all()
    # in test fixtures.
    __table_args__: tuple[SchemaItem, ...] = (
        # User-scoped unique constraints for external identifiers
        UniqueConstraint("user_id", "spotify_id", name="uq_tracks_user_spotify_id"),
        UniqueConstraint("user_id", "isrc", name="uq_tracks_user_isrc"),
        UniqueConstraint("user_id", "mbid", name="uq_tracks_user_mbid"),
        # Regular index for title searches
        Index("ix_tracks_title", "title"),
        # Composite index for Canonical Reuse normalized fuzzy lookup
        Index("ix_tracks_normalized_lookup", "title_normalized", "artist_normalized"),
        # Composite index for parenthetical-stripped fallback matching
        Index("ix_tracks_stripped_lookup", "title_stripped", "artist_normalized"),
    )


class DBConnectorTrack(BaseEntity):
    """External track representation from a specific music service.

    Represents cached track data from external APIs (Spotify, Last.fm).
    Can be recreated from external sources when needed.
    """

    __tablename__: str = "connector_tracks"

    connector_name: Mapped[str] = mapped_column(String(32))
    connector_track_identifier: Mapped[str] = mapped_column(String())
    title: Mapped[str] = mapped_column(String())
    artists: Mapped[JsonDict] = mapped_column(PgJsonb)
    album: Mapped[str | None] = mapped_column(String())
    duration_ms: Mapped[int | None]
    isrc: Mapped[str | None] = mapped_column(String(32), index=True)
    release_date: Mapped[datetime | None]
    raw_metadata: Mapped[JsonDict] = mapped_column(PgJsonb)
    last_updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )

    # Mapping relationship - plural to reflect conceptual many-to-one possibility
    mappings: Mapped[list[DBTrackMapping]] = relationship(
        back_populates="connector_track",
        passive_deletes=True,
    )

    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint("connector_name", "connector_track_identifier"),
        Index(None, "connector_name", "isrc"),
    )


class DBTrackMapping(BaseEntity):
    """Maps external connector tracks to internal canonical tracks.

    Represents the relationship between external API tracks and canonical tracks.
    Mappings can be recreated from connector data when needed.
    """

    __tablename__: str = "track_mappings"

    user_id: Mapped[str] = mapped_column(
        String(), nullable=False, default="default", server_default="default"
    )
    track_id: Mapped[UuidType] = mapped_column(
        PgUuidCol(as_uuid=True), ForeignKey("tracks.id", ondelete="CASCADE")
    )
    connector_track_id: Mapped[UuidType] = mapped_column(
        PgUuidCol(as_uuid=True),
        ForeignKey("connector_tracks.id", ondelete="CASCADE"),
    )
    connector_name: Mapped[str] = mapped_column(String(32), nullable=False)
    match_method: Mapped[str] = mapped_column(String(32))
    confidence: Mapped[int]
    confidence_evidence: Mapped[JsonDict | None] = mapped_column(PgJsonb)
    origin: Mapped[str] = mapped_column(
        String(20), nullable=False, default="automatic", server_default="automatic"
    )
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Relationships
    track: Mapped[DBTrack] = relationship(
        back_populates="mappings",
        passive_deletes=True,
    )
    connector_track: Mapped[DBConnectorTrack] = relationship(
        back_populates="mappings",
        passive_deletes=True,
    )

    __table_args__: tuple[SchemaItem, ...] = (
        # User-scoped: prevent multiple canonical tracks mapping to same connector track per user
        UniqueConstraint(
            "user_id",
            "connector_track_id",
            "connector_name",
            name="uq_track_mappings_user_connector",
        ),
        # User-scoped partial unique: only one primary per user-track-connector triple
        Index(
            "uq_primary_mapping",
            "user_id",
            "track_id",
            "connector_name",
            unique=True,
            postgresql_where=text("is_primary = TRUE"),
        ),
        # Performance indexes for common lookup patterns
        Index("ix_track_mappings_track_lookup", "track_id"),
        Index("ix_track_mappings_connector_lookup", "connector_track_id"),
        Index("ix_track_mappings_connector_name", "connector_name"),
    )


class DBMatchReview(BaseEntity):
    """Proposed track-to-connector match awaiting human review.

    Stores medium-confidence matches (between auto-reject and auto-accept
    thresholds) as a staging area separate from track_mappings. On accept,
    a real DBTrackMapping is created. On reject, the row is marked to
    prevent re-queuing the same pair.
    """

    __tablename__: str = "match_reviews"

    user_id: Mapped[str] = mapped_column(
        String(), nullable=False, default="default", server_default="default"
    )
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
    track: Mapped[DBTrack] = relationship(passive_deletes=True)
    connector_track: Mapped[DBConnectorTrack] = relationship(passive_deletes=True)

    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint(
            "user_id",
            "track_id",
            "connector_name",
            "connector_track_id",
            name="uq_match_reviews_user_track_connector",
        ),
        Index("ix_match_reviews_status", "status"),
        Index("ix_match_reviews_track_id", "track_id"),
    )


class DBTrackMetric(BaseEntity):
    """Time-series metrics for tracks from external services.

    Stores track performance metrics (play counts, listener counts) for analytics and trends.
    """

    __tablename__: str = "track_metrics"
    __table_args__: tuple[SchemaItem, ...] = (
        # Create a unique constraint - let naming convention handle the name
        UniqueConstraint("track_id", "connector_name", "metric_type"),
        # Keep the lookup index
        Index(None, "track_id", "connector_name", "metric_type"),
    )

    user_id: Mapped[str] = mapped_column(
        String(), nullable=False, default="default", server_default="default"
    )
    track_id: Mapped[UuidType] = mapped_column(
        PgUuidCol(as_uuid=True), ForeignKey("tracks.id", ondelete="CASCADE")
    )
    connector_name: Mapped[str] = mapped_column(String(32))
    metric_type: Mapped[str] = mapped_column(String(32))
    value: Mapped[float]
    collected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )

    # Relationships
    track: Mapped[DBTrack] = relationship(
        back_populates="metrics",
        passive_deletes=True,
    )


class DBTrackLike(BaseEntity):
    """Track preference state across music services.

    Represents user preferences for tracks across different services.
    """

    __tablename__: str = "track_likes"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint("user_id", "track_id", "service"),
        Index(None, "service", "is_liked"),
    )

    user_id: Mapped[str] = mapped_column(
        String(), nullable=False, default="default", server_default="default"
    )
    track_id: Mapped[UuidType] = mapped_column(
        PgUuidCol(as_uuid=True), ForeignKey("tracks.id", ondelete="CASCADE")
    )
    service: Mapped[str] = mapped_column(String(32))  # 'spotify', 'lastfm', 'mixd'
    is_liked: Mapped[bool] = mapped_column(Boolean, default=True)
    liked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_synced: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Relationships
    track: Mapped[DBTrack] = relationship(
        back_populates="likes",
        passive_deletes=True,
    )


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

    user_id: Mapped[str] = mapped_column(
        String(), nullable=False, default="default", server_default="default"
    )
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
    )


class DBConnectorPlay(BaseEntity):
    """Raw play data from external music services before resolution.

    Stores play events from external APIs (Spotify, Last.fm) with complete metadata
    for eventual resolution to canonical tracks. Follows the same pattern as
    DBConnectorTrack for separation of ingestion and resolution concerns.
    """

    __tablename__: str = "connector_plays"

    user_id: Mapped[str] = mapped_column(
        String(), nullable=False, default="default", server_default="default"
    )
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
        ),
        # Performance indexes for common queries
        Index("ix_connector_plays_connector", "connector_name"),
        Index("ix_connector_plays_played_at", "played_at"),
        Index("ix_connector_plays_resolved_track", "resolved_track_id"),
        Index(
            "ix_connector_plays_unresolved", "connector_name", "resolved_track_id"
        ),  # For finding unresolved plays
        Index("ix_connector_plays_import_batch", "import_batch_id"),
    )


class DBPlaylist(BaseEntity):
    """User playlist metadata.

    Represents user-created playlists with track associations.
    """

    __tablename__: str = "playlists"

    user_id: Mapped[str] = mapped_column(
        String(), nullable=False, default="default", server_default="default"
    )
    name: Mapped[str] = mapped_column(String())
    description: Mapped[str | None] = mapped_column(String(1000))
    track_count: Mapped[int] = mapped_column(default=0)

    # Relationships
    tracks: Mapped[list[DBPlaylistTrack]] = relationship(
        back_populates="playlist",
        cascade="all, delete-orphan",
    )
    mappings: Mapped[list[DBPlaylistMapping]] = relationship(
        back_populates="playlist",
        passive_deletes=True,
    )


class DBConnectorPlaylist(BaseEntity):
    """External service-specific playlist representation.

    Represents cached playlist data from external APIs.
    Can be recreated from external sources when needed.
    """

    __tablename__: str = "connector_playlists"

    connector_name: Mapped[str]
    connector_playlist_identifier: Mapped[str]
    name: Mapped[str]
    description: Mapped[str | None]
    owner: Mapped[str | None]
    owner_id: Mapped[str | None]
    is_public: Mapped[bool]
    collaborative: Mapped[bool] = mapped_column(default=False)
    follower_count: Mapped[int | None]
    items: Mapped[list[JsonDict]] = mapped_column(PgJsonb, default=list)
    raw_metadata: Mapped[JsonDict] = mapped_column(PgJsonb)
    # Add JSON field to store track positional information
    last_updated: Mapped[datetime]

    # Relationships
    mappings: Mapped[list[DBPlaylistMapping]] = relationship(
        back_populates="connector_playlist",
        passive_deletes=True,
    )

    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint("connector_name", "connector_playlist_identifier"),
    )


class DBPlaylistMapping(BaseEntity):
    """External service playlist mappings.

    Represents the relationship between canonical playlists and external service playlists.
    """

    __tablename__: str = "playlist_mappings"
    __table_args__: tuple[SchemaItem, ...] = (
        # Prevent one canonical playlist from having multiple mappings to same connector
        UniqueConstraint("playlist_id", "connector_name", name="uq_playlist_connector"),
        # Prevent multiple canonical playlists from claiming same external playlist
        UniqueConstraint("connector_playlist_id", name="uq_connector_playlist"),
        # Status queries for sync operations
        Index("ix_playlist_mappings_sync_status", "sync_status"),
    )

    user_id: Mapped[str] = mapped_column(
        String(), nullable=False, default="default", server_default="default"
    )
    playlist_id: Mapped[UuidType] = mapped_column(
        PgUuidCol(as_uuid=True),
        ForeignKey("playlists.id", ondelete="CASCADE"),
    )
    connector_name: Mapped[str] = mapped_column(String(32))
    connector_playlist_id: Mapped[UuidType] = mapped_column(
        PgUuidCol(as_uuid=True),
        ForeignKey("connector_playlists.id", ondelete="CASCADE"),
    )
    last_synced: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )

    # Sync management columns
    sync_direction: Mapped[str] = mapped_column(
        String(10), default="push", server_default="push"
    )
    sync_status: Mapped[str] = mapped_column(
        String(20), default="never_synced", server_default="never_synced"
    )
    last_sync_error: Mapped[str | None] = mapped_column(default=None)
    last_sync_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    last_sync_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    last_sync_tracks_added: Mapped[int | None] = mapped_column(default=None)
    last_sync_tracks_removed: Mapped[int | None] = mapped_column(default=None)

    # Relationships
    playlist: Mapped[DBPlaylist] = relationship(
        back_populates="mappings",
        passive_deletes=True,
    )
    connector_playlist: Mapped[DBConnectorPlaylist] = relationship(
        back_populates="mappings",
        passive_deletes=True,
    )


class DBPlaylistTrack(BaseEntity):
    """Playlist track membership instance with position and metadata.

    CRITICAL PRINCIPLE: Each DBPlaylistTrack record represents ONE TRACK'S
    MEMBERSHIP INSTANCE in a playlist, NOT a "position slot".

    This design enables:
    - Multiple records for the same track_id (duplicates in playlist)
    - Stable record identity through reordering (preserves added_at, etc.)
    - Independent metadata per playlist position

    Schema:
        id: Auto-increment PK representing this specific membership instance
        playlist_id: Which playlist this membership belongs to
        track_id: Which track this is (can appear multiple times)
        sort_key: Lexicographic position key (e.g., "a00000000")
        added_at: When this track was added to this position (preserved through moves)

    Examples:
        Playlist [Track A, Track B, Track A]:
        - Record 1: (id=1, playlist_id=1, track_id=5, sort_key="a00000000", added_at=2024-01-01)
        - Record 2: (id=2, playlist_id=1, track_id=6, sort_key="a00000001", added_at=2024-01-02)
        - Record 3: (id=3, playlist_id=1, track_id=5, sort_key="a00000002", added_at=2024-06-15)
                     ↑ Same track_id as Record 1, but DIFFERENT record with own added_at

        When reordering [A,B,A] → [B,A,A]:
        - Record 1: sort_key changes "a00000000" → "a00000001" (id=1 preserved!)
        - Record 2: sort_key changes "a00000001" → "a00000000" (id=2 preserved!)
        - Record 3: sort_key stays "a00000002" (id=3 preserved!)

    WARNING: Do NOT treat records as "position slots" that can be overwritten.
    Always update EXISTING records by track_id, only create new records for
    genuinely new track memberships.
    """

    __tablename__: str = "playlist_tracks"
    __table_args__: tuple[SchemaItem, ...] = (Index(None, "playlist_id", "sort_key"),)

    playlist_id: Mapped[UuidType] = mapped_column(
        PgUuidCol(as_uuid=True),
        ForeignKey("playlists.id", ondelete="CASCADE"),
    )
    track_id: Mapped[UuidType] = mapped_column(
        PgUuidCol(as_uuid=True), ForeignKey("tracks.id", ondelete="CASCADE")
    )
    sort_key: Mapped[str] = mapped_column(String(32))
    added_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=True,  # Allow NULL for historical imports where exact time is unknown
    )

    # Relationships
    playlist: Mapped[DBPlaylist] = relationship(
        back_populates="tracks",
        passive_deletes=True,
    )
    track: Mapped[DBTrack] = relationship(
        back_populates="playlist_tracks",
        passive_deletes=True,
    )


class DBWorkflow(BaseEntity):
    """Persisted workflow definition with template metadata.

    Stores the complete WorkflowDef as a JSON column alongside identity
    and template tracking fields. source_template enables upsert-by-key
    during template seeding (NULLs don't conflict in unique constraints).
    """

    __tablename__: str = "workflows"

    user_id: Mapped[str | None] = mapped_column(String(), nullable=True)
    name: Mapped[str] = mapped_column(String(), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1000))
    definition: Mapped[JsonDict] = mapped_column(PgJsonb, nullable=False)
    is_template: Mapped[bool] = mapped_column(Boolean, default=False)
    source_template: Mapped[str | None] = mapped_column(String(100))
    definition_version: Mapped[int] = mapped_column(default=1)

    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint("source_template", name="uq_workflows_source_template"),
        Index("ix_workflows_is_template", "is_template"),
        Index("ix_workflows_user_id", "user_id"),
    )


class DBWorkflowVersion(DatabaseModel, TimestampMixin):
    """Snapshot of a workflow definition at a point in time.

    Created automatically when UpdateWorkflowUseCase modifies a workflow's
    task pipeline. Stores the *previous* definition before the change.
    """

    __tablename__: str = "workflow_versions"

    workflow_id: Mapped[UuidType] = mapped_column(
        PgUuidCol(as_uuid=True),
        ForeignKey("workflows.id", ondelete="CASCADE"),
    )
    version: Mapped[int] = mapped_column(nullable=False)
    definition: Mapped[JsonDict] = mapped_column(PgJsonb, nullable=False)
    change_summary: Mapped[str | None] = mapped_column(String(1000))

    # Relationships
    workflow: Mapped[DBWorkflow] = relationship(passive_deletes=True)

    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint(
            "workflow_id", "version", name="uq_workflow_versions_workflow_version"
        ),
        Index("ix_workflow_versions_workflow_id", "workflow_id"),
    )


class DBWorkflowRun(DatabaseModel, TimestampMixin):
    """Persisted record of a single workflow execution.

    Stores the frozen definition snapshot and execution status/metrics.
    Each run has child node records tracking per-node lifecycle.
    """

    __tablename__: str = "workflow_runs"

    workflow_id: Mapped[UuidType] = mapped_column(
        PgUuidCol(as_uuid=True),
        ForeignKey("workflows.id", ondelete="CASCADE"),
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    definition_snapshot: Mapped[JsonDict] = mapped_column(PgJsonb, nullable=False)
    definition_version: Mapped[int] = mapped_column(default=1)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None]
    output_track_count: Mapped[int | None]
    output_playlist_id: Mapped[UuidType | None] = mapped_column(
        PgUuidCol(as_uuid=True), nullable=True
    )
    error_message: Mapped[str | None] = mapped_column(String(2000))
    # Serialized track summaries (track_id, title, artists, rank, metrics) —
    # see serialize_output_tracks() in application/use_cases/workflow_runs.py.
    # Uses dict[str, object] (not JsonDict) because entries contain UUIDs and
    # MetricValue datetime fields that aren't strict JSON values; psycopg
    # serializes them at write time.
    output_tracks: Mapped[list[dict[str, object]] | None] = mapped_column(
        PgJsonb, nullable=True
    )

    # Relationships
    workflow: Mapped[DBWorkflow] = relationship(passive_deletes=True)
    nodes: Mapped[list[DBWorkflowRunNode]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__: tuple[SchemaItem, ...] = (
        Index("ix_workflow_runs_workflow_id_started_at", "workflow_id", "started_at"),
        Index("ix_workflow_runs_status", "status"),
    )


class DBWorkflowRunNode(DatabaseModel):
    """Per-node execution record within a workflow run."""

    __tablename__: str = "workflow_run_nodes"

    run_id: Mapped[UuidType] = mapped_column(
        PgUuidCol(as_uuid=True),
        ForeignKey("workflow_runs.id", ondelete="CASCADE"),
    )
    node_id: Mapped[str] = mapped_column(String(100), nullable=False)
    node_type: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int] = mapped_column(default=0)
    input_track_count: Mapped[int | None]
    output_track_count: Mapped[int | None]
    error_message: Mapped[str | None] = mapped_column(String(2000))
    execution_order: Mapped[int] = mapped_column(default=0)
    # Per-node observation payload (e.g., destination playlist_changes summary).
    # See application/workflows/destination_nodes.py and observers.py for shapes.
    # dict[str, object] (not JsonDict) because nested values may include UUIDs.
    node_details: Mapped[dict[str, object] | None] = mapped_column(
        PgJsonb, nullable=True
    )

    # Relationships
    run: Mapped[DBWorkflowRun] = relationship(
        back_populates="nodes",
        passive_deletes=True,
    )

    __table_args__: tuple[SchemaItem, ...] = (
        Index("ix_workflow_run_nodes_run_id", "run_id"),
    )


class DBOAuthToken(BaseEntity):
    """Persisted OAuth tokens and session keys for external service authentication.

    One row per user per service (UNIQUE on user_id + service). Supports both
    OAuth 2.0 tokens (Spotify: access_token + refresh_token + expires_at) and
    session-based auth (Last.fm: session_key, infinite lifetime).

    Enables cloud deployment where filesystem is ephemeral — tokens survive
    container restarts without re-authentication.
    """

    __tablename__: str = "oauth_tokens"

    user_id: Mapped[str] = mapped_column(
        String(), nullable=False, default="default", server_default="default"
    )
    service: Mapped[str] = mapped_column(String(32), nullable=False)
    token_type: Mapped[str] = mapped_column(String(20), nullable=False)
    access_token: Mapped[str | None] = mapped_column(String())
    refresh_token: Mapped[str | None] = mapped_column(String())
    session_key: Mapped[str | None] = mapped_column(String())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scope: Mapped[str | None] = mapped_column(String())
    account_name: Mapped[str | None] = mapped_column(String(255))
    extra_data: Mapped[JsonDict] = mapped_column(PgJsonb, default=dict)

    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint("user_id", "service", name="uq_oauth_tokens_user_service"),
    )


class DBOAuthState(DatabaseModel):
    """Transient OAuth CSRF state for callback user association.

    Stores the CSRF state token, user_id, and PKCE code_verifier during
    OAuth flows. The callback handler consumes (deletes) the row atomically.
    Short-lived (5-minute TTL), no RLS needed — the unguessable state token
    is the access control. Uses DatabaseModel (not BaseEntity) since updated_at
    is unnecessary for ephemeral rows.
    """

    __tablename__: str = "oauth_states"

    state: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    user_id: Mapped[str] = mapped_column(String(), nullable=False)
    service: Mapped[str] = mapped_column(String(32), nullable=False)
    code_verifier: Mapped[str | None] = mapped_column(String())
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )


class DBUserSettings(BaseEntity):
    """User preferences and application settings.

    Per-user JSONB store keyed by (user_id, key). Extensible without
    migrations — new settings are just new keys in the JSONB column.
    """

    __tablename__: str = "user_settings"

    user_id: Mapped[str] = mapped_column(
        String(), nullable=False, default="default", server_default="default"
    )
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    settings: Mapped[JsonDict] = mapped_column(PgJsonb, default=dict)

    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint("user_id", "key", name="uq_user_settings_user_key"),
    )


class DBSyncCheckpoint(BaseEntity):
    """Sync state tracking for incremental operations.

    Represents synchronization state for external services.
    """

    __tablename__: str = "sync_checkpoints"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint("user_id", "service", "entity_type"),
    )

    user_id: Mapped[str] = mapped_column(String(), nullable=False)
    service: Mapped[str] = mapped_column(String(32))  # 'spotify', 'lastfm'
    entity_type: Mapped[str] = mapped_column(String(32))  # 'likes', 'plays'
    last_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cursor: Mapped[str | None] = mapped_column(String(1024))  # continuation token
    remote_total: Mapped[int | None] = mapped_column(
        nullable=True
    )  # total items reported by remote service


class DBTrackPreference(BaseEntity):
    """User preference state for a track (hmm, nah, yah, star).

    One preference per user+track pair. Source tracks where the opinion came from
    (manual, service_import, playlist_mapping). preferred_at preserves the original
    timestamp from the source service.
    """

    __tablename__: str = "track_preferences"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint("user_id", "track_id"),
        Index("ix_track_preferences_user_id_state", "user_id", "state"),
        Index("ix_track_preferences_user_id_preferred_at", "user_id", "preferred_at"),
    )

    user_id: Mapped[str] = mapped_column(
        String(), nullable=False, default="default", server_default="default"
    )
    track_id: Mapped[UuidType] = mapped_column(
        PgUuidCol(as_uuid=True), ForeignKey("tracks.id", ondelete="CASCADE")
    )
    state: Mapped[str] = mapped_column(String(16))  # hmm, nah, yah, star
    source: Mapped[str] = mapped_column(
        String(32)
    )  # manual, service_import, playlist_mapping
    preferred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # Relationships
    track: Mapped[DBTrack] = relationship(
        back_populates="preferences",
        passive_deletes=True,
    )


class DBTrackPreferenceEvent(BaseEntity):
    """Append-only log of preference changes.

    Events are never updated or deleted. Captures the full timeline of
    preference changes so "when did I first yah this?" is always answerable.
    """

    __tablename__: str = "track_preference_events"
    __table_args__: tuple[SchemaItem, ...] = (
        Index("ix_track_preference_events_user_id_track_id", "user_id", "track_id"),
    )

    user_id: Mapped[str] = mapped_column(
        String(), nullable=False, default="default", server_default="default"
    )
    track_id: Mapped[UuidType] = mapped_column(
        PgUuidCol(as_uuid=True), ForeignKey("tracks.id", ondelete="CASCADE")
    )
    old_state: Mapped[str | None] = mapped_column(String(16))
    new_state: Mapped[str | None] = mapped_column(String(16))
    source: Mapped[str] = mapped_column(String(32))
    preferred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
