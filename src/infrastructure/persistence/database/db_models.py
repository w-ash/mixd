"""SQLAlchemy database models for the Narada music platform.

This module defines the core domain entities and their relationships using
SQLAlchemy 2.0 patterns with proper type annotations and relationship definitions.
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
    Select,
    String,
    UniqueConstraint,
    select,
    text,
)
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from src.config import get_logger

# Create module logger
logger = get_logger(__name__)

# Define naming convention for constraints
convention = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",  # Index
    "uq": "uq_%(table_name)s_%(column_0_label)s",  # Unique constraint
    "ck": "ck_%(table_name)s_%(constraint_name)s",  # Check constraint
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",  # Foreign key
    "pk": "pk_%(table_name)s",  # Primary key
}

# Create metadata with naming convention
metadata = MetaData(naming_convention=convention)


class NaradaDBBase(AsyncAttrs, DeclarativeBase):
    """Base class for all database models with timestamps and soft delete."""

    # Use the metadata with naming convention
    metadata = metadata

    id: Mapped[int] = mapped_column(primary_key=True)  # Remove _position_in_table
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
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)

    def mark_soft_deleted(self) -> None:
        """Mark record as logically deleted (soft delete)."""
        self.is_deleted = True
        self.deleted_at = datetime.now(UTC)

    @classmethod
    def active_records(cls) -> Select:
        """Return a select statement for non-deleted records."""
        return select(cls).where(cls.is_deleted == False)  # noqa: E712


class DBTrack(NaradaDBBase):
    """Core track entity with essential metadata."""

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
    mappings: Mapped[list["DBTrackMapping"]] = relationship(
        back_populates="track",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    metrics: Mapped[list["DBTrackMetric"]] = relationship(
        back_populates="track",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    likes: Mapped[list["DBTrackLike"]] = relationship(
        back_populates="track",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    plays: Mapped[list["DBTrackPlay"]] = relationship(
        back_populates="track",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    playlist_tracks: Mapped[list["DBPlaylistTrack"]] = relationship(
        back_populates="track",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    # Add table constraints - simplified to rely on naming convention
    __table_args__ = (
        UniqueConstraint("isrc"),
        UniqueConstraint("spotify_id"),
        UniqueConstraint("mbid"),
        Index(None, "title"),  # Let naming convention handle the name
    )


class DBConnectorTrack(NaradaDBBase):
    """External track representation from a specific music service."""

    __tablename__ = "connector_tracks"

    connector_name: Mapped[str] = mapped_column(String(32))
    connector_track_id: Mapped[str] = mapped_column(String(64))
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
    mappings: Mapped[list["DBTrackMapping"]] = relationship(
        back_populates="connector_track",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        UniqueConstraint("connector_name", "connector_track_id"),
        Index(None, "connector_name", "isrc"),
    )


class DBTrackMapping(NaradaDBBase):
    """Maps external connector tracks to internal canonical tracks."""

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
    track: Mapped["DBTrack"] = relationship(
        back_populates="mappings",
        passive_deletes=True,
    )
    connector_track: Mapped["DBConnectorTrack"] = relationship(
        back_populates="mappings",
        passive_deletes=True,
    )

    __table_args__ = (
        # Existing unique constraint for mapping integrity
        UniqueConstraint("track_id", "connector_track_id"),
        # NEW: Partial unique constraint - only one primary per track-connector pair
        Index(
            "uq_primary_mapping",
            "track_id",
            "connector_name",
            unique=True,
            sqlite_where=text("is_primary = TRUE"),
        ),
        # Performance indexes for common lookup patterns
        Index(
            "ix_track_mappings_track_lookup",
            "track_id",
            sqlite_where=text("is_deleted = FALSE"),
        ),
        Index(
            "ix_track_mappings_connector_lookup",
            "connector_track_id",
            sqlite_where=text("is_deleted = FALSE"),
        ),
        Index(
            "ix_track_mappings_connector_name",
            "connector_name",
            sqlite_where=text("is_deleted = FALSE"),
        ),
    )


class DBTrackMetric(NaradaDBBase):
    """Time-series metrics for tracks from external services."""

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


class DBTrackLike(NaradaDBBase):
    """Track preference state across music services."""

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
    track: Mapped["DBTrack"] = relationship(
        back_populates="likes",
        passive_deletes=True,
    )


class DBTrackPlay(NaradaDBBase):
    """Immutable record of track plays across services."""

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


class DBPlaylist(NaradaDBBase):
    """User playlist metadata."""

    __tablename__ = "playlists"

    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(String(1000))
    track_count: Mapped[int] = mapped_column(default=0)

    # Relationships
    tracks: Mapped[list["DBPlaylistTrack"]] = relationship(
        back_populates="playlist",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    mappings: Mapped[list["DBPlaylistMapping"]] = relationship(
        back_populates="playlist",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class DBConnectorPlaylist(NaradaDBBase):
    """External service-specific playlist representation."""

    __tablename__ = "connector_playlists"

    connector_name: Mapped[str]
    connector_playlist_id: Mapped[str]
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

    __table_args__ = (UniqueConstraint("connector_name", "connector_playlist_id"),)


class DBPlaylistMapping(NaradaDBBase):
    """External service playlist mappings."""

    __tablename__ = "playlist_mappings"
    __table_args__ = (
        # Prevent one canonical playlist from having multiple mappings to same connector
        Index(
            "uq_playlist_connector_active",
            "playlist_id",
            "connector_name",
            unique=True,
            sqlite_where=text("is_deleted = 0"),
        ),
        # Prevent multiple canonical playlists from claiming same external playlist
        Index(
            "uq_connector_playlist_active",
            "connector_name",
            "connector_playlist_id",
            unique=True,
            sqlite_where=text("is_deleted = 0"),
        ),
    )

    playlist_id: Mapped[int] = mapped_column(
        ForeignKey("playlists.id", ondelete="CASCADE"),
    )
    connector_name: Mapped[str] = mapped_column(String(32))
    connector_playlist_id: Mapped[str] = mapped_column(String(64))
    last_synced: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )

    # Relationships
    playlist: Mapped[DBPlaylist] = relationship(
        back_populates="mappings",
        passive_deletes=True,
    )


class DBPlaylistTrack(NaradaDBBase):
    """Playlist track ordering and metadata."""

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


class DBSyncCheckpoint(NaradaDBBase):
    """Sync state tracking for incremental operations."""

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
            await conn.run_sync(NaradaDBBase.metadata.create_all)
            logger.info("Database schema verified - all tables exist")

    except Exception as e:
        logger.error(f"Database initialization failed: {e}")
        raise
    else:
        logger.info("Database schema initialization complete")
