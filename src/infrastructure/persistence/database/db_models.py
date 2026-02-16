"""SQLAlchemy database models for the Narada music platform.

This module implements a simplified architecture using hard deletes for all entities,
following SQLAlchemy 2.0 best practices with modern type annotations.

Architecture:
- DatabaseModel: Single DeclarativeBase foundation for all entities
- TimestampMixin: Provides created_at/updated_at for audit trails

All entities use hard deletes for simplicity and performance.
Data recovery relies on external API re-import and database backups.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    MetaData,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)

from src.config import get_logger

# Create module logger
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
    """

    metadata = metadata

    id: Mapped[int] = mapped_column(primary_key=True, sort_order=-1)


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

    __abstract__ = True


class DBTrack(BaseEntity):
    """Core track entity with essential metadata.

    Represents the user's music library with plays, likes, and playlist associations.
    Uses hard deletes for simplicity and performance.
    """

    __tablename__ = "tracks"

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    artists: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    album: Mapped[str | None] = mapped_column(String(255))
    duration_ms: Mapped[int | None]
    release_date: Mapped[datetime | None]
    isrc: Mapped[str | None] = mapped_column(String(32), index=True)
    spotify_id: Mapped[str | None] = mapped_column(String(64), index=True)
    mbid: Mapped[str | None] = mapped_column(String(36), index=True)

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

    # Standard unique constraints that work with SQLite bulk upsert
    __table_args__ = (
        # Standard unique constraints for external identifiers
        UniqueConstraint("spotify_id", name="uq_tracks_spotify_id"),
        UniqueConstraint("isrc", name="uq_tracks_isrc"),
        UniqueConstraint("mbid", name="uq_tracks_mbid"),
        # Regular index for title searches
        Index("ix_tracks_title", "title"),
    )


class DBConnectorTrack(BaseEntity):
    """External track representation from a specific music service.

    Represents cached track data from external APIs (Spotify, Last.fm).
    Can be recreated from external sources when needed.
    """

    __tablename__ = "connector_tracks"

    connector_name: Mapped[str] = mapped_column(String(32))
    connector_track_identifier: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(255))
    artists: Mapped[dict[str, Any]] = mapped_column(JSON)
    album: Mapped[str | None] = mapped_column(String(255))
    duration_ms: Mapped[int | None]
    isrc: Mapped[str | None] = mapped_column(String(32), index=True)
    release_date: Mapped[datetime | None]
    raw_metadata: Mapped[dict[str, Any]] = mapped_column(JSON)
    last_updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )

    # Mapping relationship - plural to reflect conceptual many-to-one possibility
    mappings: Mapped[list[DBTrackMapping]] = relationship(
        back_populates="connector_track",
        passive_deletes=True,
    )

    __table_args__ = (
        UniqueConstraint("connector_name", "connector_track_identifier"),
        Index(None, "connector_name", "isrc"),
    )


class DBTrackMapping(BaseEntity):
    """Maps external connector tracks to internal canonical tracks.

    Represents the relationship between external API tracks and canonical tracks.
    Mappings can be recreated from connector data when needed.
    """

    __tablename__ = "track_mappings"

    track_id: Mapped[int] = mapped_column(ForeignKey("tracks.id", ondelete="CASCADE"))
    connector_track_id: Mapped[int] = mapped_column(
        ForeignKey("connector_tracks.id", ondelete="CASCADE"),
    )
    connector_name: Mapped[str] = mapped_column(String(32), nullable=False)
    match_method: Mapped[str] = mapped_column(String(32))
    confidence: Mapped[int]
    confidence_evidence: Mapped[dict[str, Any] | None] = mapped_column(JSON)
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

    __table_args__ = (
        # CRITICAL: Prevent multiple canonical tracks mapping to same connector track
        UniqueConstraint(
            "connector_track_id",
            "connector_name",
            name="uq_connector_track_canonical_mapping",
        ),
        # NEW: Partial unique constraint - only one primary per track-connector pair
        Index(
            "uq_primary_mapping",
            "track_id",
            "connector_name",
            unique=True,
            sqlite_where=text("is_primary = TRUE"),
        ),
        # Performance indexes for common lookup patterns
        Index("ix_track_mappings_track_lookup", "track_id"),
        Index("ix_track_mappings_connector_lookup", "connector_track_id"),
        Index("ix_track_mappings_connector_name", "connector_name"),
    )


class DBTrackMetric(BaseEntity):
    """Time-series metrics for tracks from external services.

    Stores track performance metrics (play counts, popularity scores) for analytics and trends.
    """

    __tablename__ = "track_metrics"
    __table_args__ = (
        # Create a unique constraint - let naming convention handle the name
        UniqueConstraint("track_id", "connector_name", "metric_type"),
        # Keep the lookup index
        Index(None, "track_id", "connector_name", "metric_type"),
    )

    track_id: Mapped[int] = mapped_column(ForeignKey("tracks.id", ondelete="CASCADE"))
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

    __tablename__ = "track_likes"
    __table_args__ = (
        UniqueConstraint("track_id", "service"),
        Index(None, "service", "is_liked"),
    )

    # Core fields
    track_id: Mapped[int] = mapped_column(ForeignKey("tracks.id", ondelete="CASCADE"))
    service: Mapped[str] = mapped_column(String(32))  # 'spotify', 'lastfm', 'internal'
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

    __tablename__ = "track_plays"
    __table_args__ = (
        # Unique constraint to prevent duplicate plays (safety net for application-level deduplication)
        UniqueConstraint(
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
    )

    # Core fields
    track_id: Mapped[int] = mapped_column(ForeignKey("tracks.id", ondelete="CASCADE"))
    service: Mapped[str] = mapped_column(String(32))  # 'spotify', 'lastfm', 'internal'
    played_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    ms_played: Mapped[int | None]
    context: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    # Import tracking (service-agnostic)
    import_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    import_source: Mapped[str | None] = mapped_column(
        String(32)
    )  # 'spotify_export', 'lastfm_api', 'manual'
    import_batch_id: Mapped[str | None] = mapped_column(String(64))

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

    __tablename__ = "connector_plays"

    # Connector identification
    connector_name: Mapped[str] = mapped_column(String(32))  # "lastfm", "spotify"
    connector_track_identifier: Mapped[str] = mapped_column(
        String(255)
    )  # "artist::title" for lastfm

    # Play event data
    played_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    ms_played: Mapped[int | None]

    # Raw API data preservation
    raw_metadata: Mapped[dict[str, Any]] = mapped_column(JSON)

    # Resolution tracking (nullable until resolved)
    resolved_track_id: Mapped[int | None] = mapped_column(
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
    import_batch_id: Mapped[str | None] = mapped_column(String(64))

    # Relationships (only to resolved track, if any)
    resolved_track: Mapped[DBTrack | None] = relationship(
        back_populates="connector_plays",
        passive_deletes=True,
    )

    __table_args__ = (
        # Prevent duplicate connector plays (same as track_plays deduplication pattern)
        UniqueConstraint(
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

    __tablename__ = "playlists"

    name: Mapped[str] = mapped_column(String(255))
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

    __tablename__ = "connector_playlists"

    connector_name: Mapped[str]
    connector_playlist_identifier: Mapped[str]
    name: Mapped[str]
    description: Mapped[str | None]
    owner: Mapped[str | None]
    owner_id: Mapped[str | None]
    is_public: Mapped[bool]
    collaborative: Mapped[bool] = mapped_column(default=False)
    follower_count: Mapped[int | None]
    items: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    raw_metadata: Mapped[dict[str, Any]] = mapped_column(JSON)
    # Add JSON field to store track positional information
    last_updated: Mapped[datetime]

    # Relationships
    mappings: Mapped[list[DBPlaylistMapping]] = relationship(
        back_populates="connector_playlist",
        passive_deletes=True,
    )

    __table_args__ = (
        UniqueConstraint("connector_name", "connector_playlist_identifier"),
    )


class DBPlaylistMapping(BaseEntity):
    """External service playlist mappings.

    Represents the relationship between canonical playlists and external service playlists.
    """

    __tablename__ = "playlist_mappings"
    __table_args__ = (
        # Prevent one canonical playlist from having multiple mappings to same connector
        UniqueConstraint("playlist_id", "connector_name", name="uq_playlist_connector"),
        # Prevent multiple canonical playlists from claiming same external playlist
        UniqueConstraint("connector_playlist_id", name="uq_connector_playlist"),
    )

    playlist_id: Mapped[int] = mapped_column(
        ForeignKey("playlists.id", ondelete="CASCADE"),
    )
    connector_name: Mapped[str] = mapped_column(String(32))
    connector_playlist_id: Mapped[int] = mapped_column(
        ForeignKey("connector_playlists.id", ondelete="CASCADE")
    )
    last_synced: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )

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

    __tablename__ = "playlist_tracks"
    __table_args__ = (Index(None, "playlist_id", "sort_key"),)

    playlist_id: Mapped[int] = mapped_column(
        ForeignKey("playlists.id", ondelete="CASCADE"),
    )
    track_id: Mapped[int] = mapped_column(ForeignKey("tracks.id", ondelete="CASCADE"))
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


class DBSyncCheckpoint(BaseEntity):
    """Sync state tracking for incremental operations.

    Represents synchronization state for external services.
    """

    __tablename__ = "sync_checkpoints"
    __table_args__ = (UniqueConstraint("user_id", "service", "entity_type"),)

    user_id: Mapped[str] = mapped_column(String(64))
    service: Mapped[str] = mapped_column(String(32))  # 'spotify', 'lastfm'
    entity_type: Mapped[str] = mapped_column(String(32))  # 'likes', 'plays'
    last_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cursor: Mapped[str | None] = mapped_column(String(1024))  # continuation token


async def init_db() -> None:
    """Initialize database schema.

    Creates all tables if they don't exist.
    This is a safe operation that won't affect existing data.
    """
    from sqlalchemy import inspect

    from src.infrastructure.persistence.database.db_connection import get_engine

    engine = get_engine()

    try:
        # First check if tables exist (for informational purposes)
        async with engine.connect() as conn:
            inspector = await conn.run_sync(inspect)
            existing_tables = await conn.run_sync(lambda _: inspector.get_table_names())
            has_tables = bool(existing_tables)

            if has_tables:
                logger.info(f"Found existing tables: {existing_tables}")

        # Create tables - SQLAlchemy will skip tables that already exist
        async with engine.begin() as conn:
            await conn.run_sync(DatabaseModel.metadata.create_all)
            logger.info("Database schema verified - all tables exist")

    except Exception as e:
        logger.error(f"Database initialization failed: {e}")
        raise
    else:
        logger.info("Database schema initialization complete")
