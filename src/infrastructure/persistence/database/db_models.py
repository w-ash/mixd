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

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, ClassVar, cast
import uuid as uuid_mod

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    MetaData,
    String,
    UniqueConstraint,
    inspect,
    text,
)
from sqlalchemy.dialects import postgresql as pg_dialect
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import (
    NO_VALUE,
    DeclarativeBase,
    InstrumentedAttribute,
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

    def loaded_list[T](
        self, attribute: InstrumentedAttribute[Sequence[T]], item_type: type[T]
    ) -> list[T]:
        """Read an eager-loaded ``*``-to-many relationship as a typed list.

        Zero I/O: ``AttributeState.loaded_value`` is a plain dict lookup against
        the instance state (``state.dict.get(key, NO_VALUE)``), so this can never
        emit SQL or hit a greenlet boundary. Returns ``[]`` when the relationship
        was not eager-loaded — a forgotten ``selectinload`` degrades gracefully
        instead of lazy-loading. ``item_type`` narrows ``loaded_value`` (typed
        ``Any`` in the stubs) and filters out any element of the wrong type.

        Pass the ORM descriptor directly: ``model.loaded_list(DBPlaylist.tracks,
        DBPlaylistTrack)``.
        """
        value = cast(object, inspect(self).attrs[attribute.key].loaded_value)
        if value is NO_VALUE or not isinstance(value, list):
            return []
        return [
            item for item in cast("list[object]", value) if isinstance(item, item_type)
        ]

    def loaded_one[T](
        self, attribute: InstrumentedAttribute[T], item_type: type[T]
    ) -> T | None:
        """Read an eager-loaded ``*``-to-one relationship as a typed value-or-None.

        The to-one counterpart of :meth:`loaded_list`. Zero I/O; returns ``None``
        when the relationship was not eager-loaded (``loaded_value`` is ``NO_VALUE``,
        which fails the ``isinstance`` check) or holds the wrong type.

        Pass the ORM descriptor directly: ``mapping.loaded_one(
        DBTrackMapping.connector_track, DBConnectorTrack)``.
        """
        value = cast(object, inspect(self).attrs[attribute.key].loaded_value)
        return value if isinstance(value, item_type) else None


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
        lazy="raise_on_sql",
    )
    metrics: Mapped[list[DBTrackMetric]] = relationship(
        back_populates="track",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise_on_sql",
    )
    likes: Mapped[list[DBTrackLike]] = relationship(
        back_populates="track",
        cascade="all, delete-orphan",
        lazy="raise_on_sql",
        passive_deletes=True,
    )
    plays: Mapped[list[DBTrackPlay]] = relationship(
        back_populates="track",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise_on_sql",
    )
    connector_plays: Mapped[list[DBConnectorPlay]] = relationship(
        back_populates="resolved_track",
        passive_deletes=True,
        lazy="raise_on_sql",
    )
    preferences: Mapped[list[DBTrackPreference]] = relationship(
        back_populates="track",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise_on_sql",
    )
    tags: Mapped[list[DBTrackTag]] = relationship(
        back_populates="track",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise_on_sql",
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
        lazy="raise_on_sql",
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
        lazy="raise_on_sql",
    )
    connector_track: Mapped[DBConnectorTrack] = relationship(
        back_populates="mappings",
        passive_deletes=True,
        lazy="raise_on_sql",
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
        lazy="raise_on_sql",
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
        lazy="raise_on_sql",
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
        lazy="raise_on_sql",
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
        lazy="raise_on_sql",
        passive_deletes=True,
    )
    mappings: Mapped[list[DBPlaylistMapping]] = relationship(
        back_populates="playlist",
        passive_deletes=True,
        lazy="raise_on_sql",
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
    snapshot_id: Mapped[str | None] = mapped_column(String(64), default=None)
    # Add JSON field to store track positional information
    last_updated: Mapped[datetime]

    # Relationships
    mappings: Mapped[list[DBPlaylistMapping]] = relationship(
        back_populates="connector_playlist",
        passive_deletes=True,
        lazy="raise_on_sql",
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
        # One canonical per (user, external playlist). Scoping to user lets two
        # users own separate local playlists for the same Spotify/Last.fm URL.
        UniqueConstraint(
            "user_id", "connector_playlist_id", name="uq_user_connector_playlist"
        ),
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
    last_sync_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    last_sync_tracks_added: Mapped[int | None] = mapped_column(default=None)
    last_sync_tracks_removed: Mapped[int | None] = mapped_column(default=None)
    last_sync_tracks_unmatched: Mapped[int | None] = mapped_column(default=None)

    # Relationships
    playlist: Mapped[DBPlaylist] = relationship(
        back_populates="mappings",
        passive_deletes=True,
        lazy="raise_on_sql",
    )
    connector_playlist: Mapped[DBConnectorPlaylist] = relationship(
        back_populates="mappings",
        passive_deletes=True,
        lazy="raise_on_sql",
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
    __table_args__: tuple[SchemaItem, ...] = (
        Index(None, "playlist_id", "sort_key"),
        # Every membership row is either RESOLVED (track_id set) or UNRESOLVED
        # with a display snapshot (unresolved_metadata set). A position can never
        # be a pure hole — this is the structural guarantee that an imported
        # playlist is always complete (right count + order), even for Spotify
        # local/unavailable tracks that have no connector_tracks row to point at.
        # connector_track_id is a best-effort re-resolution FK, hence not required.
        CheckConstraint(
            "track_id IS NOT NULL OR unresolved_metadata IS NOT NULL",
            name="resolved_or_source",
        ),
        # Cheap lookup for the "N unresolved" badge and the re-resolution pass.
        Index(
            "ix_playlist_tracks_unresolved",
            "playlist_id",
            postgresql_where=text("track_id IS NULL"),
        ),
    )

    playlist_id: Mapped[UuidType] = mapped_column(
        PgUuidCol(as_uuid=True),
        ForeignKey("playlists.id", ondelete="CASCADE"),
    )
    # Nullable: NULL marks an UNRESOLVED membership — a source playlist position
    # whose connector track could not be matched/ingested to a canonical track.
    track_id: Mapped[UuidType | None] = mapped_column(
        PgUuidCol(as_uuid=True), ForeignKey("tracks.id", ondelete="CASCADE")
    )
    # Set on unresolved rows (and optionally on resolved rows as provenance):
    # the connector track this position came from. Drives re-resolution — a
    # query against track_mappings by (connector, connector_track_id) — without
    # parsing JSON. ON DELETE SET NULL so pruning a connector track never
    # orphans a playlist position.
    connector_track_id: Mapped[UuidType | None] = mapped_column(
        PgUuidCol(as_uuid=True),
        ForeignKey("connector_tracks.id", ondelete="SET NULL"),
    )
    # Display snapshot for unresolved rows (title/artists/connector identifier)
    # so the UI can render "Couldn't match: <title> — <artist>" with no join.
    unresolved_metadata: Mapped[JsonDict | None] = mapped_column(PgJsonb)
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
        lazy="raise_on_sql",
    )
    track: Mapped[DBTrack | None] = relationship(
        passive_deletes=True,
        lazy="raise_on_sql",
    )


class DBPlaylistSyncBase(BaseEntity):
    """Per-link snapshot id a playlist link last reconciled to.

    User/link-scoped (unlike the global ``connector_playlists`` cache, which is
    shared across users and overwritten on any fetch), so it cannot leak across
    tenants. Recorded on every apply; the foundation for a future snapshot
    fast-skip and 3-way (bidirectional) merge.

    One row per link (``uq_playlist_sync_bases_link``).
    """

    __tablename__: str = "playlist_sync_bases"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint("link_id", name="uq_playlist_sync_bases_link"),
        Index("ix_playlist_sync_bases_user", "user_id"),
    )

    user_id: Mapped[str] = mapped_column(
        String(), nullable=False, default="default", server_default="default"
    )
    link_id: Mapped[UuidType] = mapped_column(
        PgUuidCol(as_uuid=True),
        ForeignKey("playlist_mappings.id", ondelete="CASCADE"),
    )
    connector_name: Mapped[str] = mapped_column(String(32))
    connector_playlist_identifier: Mapped[str] = mapped_column(String())
    # The connector snapshot id at the moment this base was recorded. When the
    # next fetch returns the same snapshot id, nothing changed remotely → the
    # apply is a no-op (the idempotency the old snapshot_id was never used for).
    base_snapshot_id: Mapped[str | None] = mapped_column(String(64), default=None)
    base_taken_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DBWorkflow(BaseEntity):
    """Persisted, user-owned workflow definition.

    Stores the complete WorkflowDef as a JSON column alongside identity.
    Every row is a user-owned, editable workflow; built-in templates are a
    file-backed gallery (``list_workflow_defs``), not rows in this table.
    """

    __tablename__: str = "workflows"

    user_id: Mapped[str | None] = mapped_column(String(), nullable=True)
    name: Mapped[str] = mapped_column(String(), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1000))
    definition: Mapped[JsonDict] = mapped_column(PgJsonb, nullable=False)
    definition_version: Mapped[int] = mapped_column(default=1)

    __table_args__: tuple[SchemaItem, ...] = (Index("ix_workflows_user_id", "user_id"),)


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
    workflow: Mapped[DBWorkflow] = relationship(
        passive_deletes=True, lazy="raise_on_sql"
    )

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
    # Per-workflow sequential run number (1, 2, 3 …) — the human-facing run
    # identity the UI shows instead of the UUID. Assigned at create_run as
    # MAX(run_number)+1 for the workflow (migration 027). The server_default="0"
    # matches the migration (deploy-window safety for the prior release) and keeps
    # autogenerate from flagging drift; create_run always sets the real value.
    run_number: Mapped[int] = mapped_column(nullable=False, server_default=text("0"))
    # SSE registry's queue key. Lets the snapshot endpoint resolve
    # operation_id -> run row without the in-memory registry, so a
    # restarted Fly machine still answers /operations/{id}/snapshot.
    operation_id: Mapped[str | None] = mapped_column(
        String(36),
        unique=True,
        index=True,
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    definition_snapshot: Mapped[JsonDict] = mapped_column(PgJsonb, nullable=False)
    definition_version: Mapped[int] = mapped_column(default=1)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None]
    output_track_count: Mapped[int | None]
    output_playlist_id: Mapped[UuidType | None] = mapped_column(
        PgUuidCol(as_uuid=True), nullable=True
    )
    error_message: Mapped[str | None] = mapped_column(String(2000))
    # Serialized track summaries (track_id, title, artists, rank, metrics) —
    # see serialize_output_tracks() in application/use_cases/workflow_runs.py.
    # That builder stringifies UUIDs and ISO-formats datetimes for the
    # benefit of in-process consumers (preview, CLI). orjson is wired in
    # as the psycopg JSONB dumper at engine init (db_connection.py), so
    # any raw UUID/datetime values that reach the write path are also
    # serialized natively.
    output_tracks: Mapped[list[dict[str, object]] | None] = mapped_column(
        PgJsonb, nullable=True
    )
    # Provenance: the schedule that fired this run, if any. ON DELETE SET NULL
    # so deleting a schedule preserves its historical runs (migration 026).
    triggered_by_schedule_id: Mapped[UuidType | None] = mapped_column(
        PgUuidCol(as_uuid=True),
        ForeignKey("schedules.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Relationships
    workflow: Mapped[DBWorkflow] = relationship(
        passive_deletes=True, lazy="raise_on_sql"
    )
    nodes: Mapped[list[DBWorkflowRunNode]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise_on_sql",
    )

    __table_args__: tuple[SchemaItem, ...] = (
        Index("ix_workflow_runs_workflow_id_started_at", "workflow_id", "started_at"),
        Index("ix_workflow_runs_status", "status"),
        # At most one active (pending/running) run per workflow — the DB-backed
        # concurrency guard. Mirrors migration 024; declared here because
        # integration tests build the schema via metadata.create_all, bypassing
        # the migration chain. The repository maps this constraint's
        # IntegrityError to WorkflowAlreadyRunningError (409).
        Index(
            "uq_workflow_runs_active",
            "workflow_id",
            unique=True,
            postgresql_where=text("status IN ('pending', 'running')"),
        ),
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
    # The canonical producer is build_playlist_changes in
    # use_cases/_shared/playlist_results.py, which stringifies UUIDs at the
    # boundary for in-process consumers. orjson is wired in as the
    # psycopg JSONB dumper at engine init (db_connection.py), so a new
    # producer that forgets the rule and emits raw UUIDs / datetimes
    # won't crash at flush time.
    node_details: Mapped[dict[str, object] | None] = mapped_column(
        PgJsonb, nullable=True
    )

    # Relationships
    run: Mapped[DBWorkflowRun] = relationship(
        back_populates="nodes",
        passive_deletes=True,
        lazy="raise_on_sql",
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
    (manual, service_import, playlist_assignment). preferred_at preserves the original
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
    )  # manual, service_import, playlist_assignment
    preferred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # Relationships
    track: Mapped[DBTrack] = relationship(
        back_populates="preferences",
        passive_deletes=True,
        lazy="raise_on_sql",
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


class DBTrackTag(BaseEntity):
    """User-assigned tag on a track (mood:chill, energy:high, banger).

    A track can carry many tags per user — UNIQUE key is three-part
    (user_id, track_id, tag). ``namespace`` / ``value`` are derived from
    ``tag`` at the domain layer and stored here so the DB can index them
    directly. ``tagged_at`` preserves the original timestamp from the
    source action (manual click, service import, playlist mapping).
    """

    __tablename__: str = "track_tags"

    # NOTE: GIN trigram index on `tag` (ix_track_tags_tag_trgm) is created in
    # migration c602c5a08631 only — it requires the pg_trgm extension and
    # would fail with metadata.create_all() in test fixtures.
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint("user_id", "track_id", "tag"),
        Index("ix_track_tags_user_id_tag", "user_id", "tag"),
        Index("ix_track_tags_user_id_namespace", "user_id", "namespace"),
        Index("ix_track_tags_user_id_tagged_at", "user_id", "tagged_at"),
    )

    user_id: Mapped[str] = mapped_column(
        String(), nullable=False, default="default", server_default="default"
    )
    track_id: Mapped[UuidType] = mapped_column(
        PgUuidCol(as_uuid=True), ForeignKey("tracks.id", ondelete="CASCADE")
    )
    tag: Mapped[str] = mapped_column(String(64))
    namespace: Mapped[str | None] = mapped_column(String(32))
    value: Mapped[str] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(
        String(32)
    )  # manual, service_import, playlist_assignment
    tagged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    track: Mapped[DBTrack] = relationship(
        back_populates="tags",
        passive_deletes=True,
        lazy="raise_on_sql",
    )


class DBTrackTagEvent(BaseEntity):
    """Append-only log of tag add/remove events.

    Events are never updated or deleted. Captures the full timeline so
    "when did I first tag this as chill?" stays answerable even after
    the tag is later removed.
    """

    __tablename__: str = "track_tag_events"
    __table_args__: tuple[SchemaItem, ...] = (
        Index("ix_track_tag_events_user_id_track_id", "user_id", "track_id"),
    )

    user_id: Mapped[str] = mapped_column(
        String(), nullable=False, default="default", server_default="default"
    )
    track_id: Mapped[UuidType] = mapped_column(
        PgUuidCol(as_uuid=True), ForeignKey("tracks.id", ondelete="CASCADE")
    )
    tag: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(8))  # add, remove
    source: Mapped[str] = mapped_column(String(32))
    tagged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DBPlaylistAssignment(BaseEntity):
    """One metadata action bound to a cached connector playlist.

    Applied to every track in the playlist on the next assignment apply —
    either a preference state ("star", "nah", ...) or a normalized tag.
    One connector playlist can carry multiple assignments (a "Workout
    Starred" playlist might assign BOTH ``set_preference=star`` AND
    ``add_tag=context:workout``).

    FKs to ``connector_playlists.id`` (the cached connector playlist),
    NOT to a canonical Mixd ``Playlist`` — canonical playlists and
    ``PlaylistLink`` are optional; the assignment drives tag application
    via the existing ``ConnectorTrack → Track`` resolution.
    """

    __tablename__: str = "playlist_assignments"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint(
            "connector_playlist_id",
            "action_type",
            "action_value",
            name="uq_playlist_assignments_action",
        ),
        Index(
            "ix_playlist_assignments_user_id",
            "user_id",
        ),
    )

    user_id: Mapped[str] = mapped_column(
        String(), nullable=False, default="default", server_default="default"
    )
    connector_playlist_id: Mapped[UuidType] = mapped_column(
        PgUuidCol(as_uuid=True),
        ForeignKey("connector_playlists.id", ondelete="CASCADE"),
        nullable=False,
    )
    action_type: Mapped[str] = mapped_column(String(16))  # set_preference, add_tag
    action_value: Mapped[str] = mapped_column(
        String(64)
    )  # hmm/nah/yah/star, or normalized tag

    members: Mapped[list[DBPlaylistAssignmentMember]] = relationship(
        back_populates="assignment",
        passive_deletes=True,
        cascade="all, delete-orphan",
        lazy="raise_on_sql",
    )


class DBPlaylistAssignmentMember(BaseEntity):
    """Snapshot of which canonical tracks matched an assignment on last apply.

    Replaced (DELETE + INSERT) on every apply so membership diffs —
    "this track was in the Starred playlist last time, now it's not" —
    are computable without accumulation errors. ``synced_at`` carries the
    apply timestamp for conflict-detection tiebreakers.

    ``user_id`` is denormalized from the parent assignment so RLS isolates
    direct queries against this table (matches the 015 child-table RLS
    pattern).
    """

    __tablename__: str = "playlist_assignment_members"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint(
            "assignment_id", "track_id", name="uq_playlist_assignment_members_pair"
        ),
        Index("ix_playlist_assignment_members_assignment_id", "assignment_id"),
    )

    user_id: Mapped[str] = mapped_column(
        String(), nullable=False, default="default", server_default="default"
    )
    assignment_id: Mapped[UuidType] = mapped_column(
        PgUuidCol(as_uuid=True),
        ForeignKey("playlist_assignments.id", ondelete="CASCADE"),
        nullable=False,
    )
    track_id: Mapped[UuidType] = mapped_column(
        PgUuidCol(as_uuid=True),
        ForeignKey("tracks.id", ondelete="CASCADE"),
        nullable=False,
    )
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    assignment: Mapped[DBPlaylistAssignment] = relationship(
        back_populates="members",
        passive_deletes=True,
        lazy="raise_on_sql",
    )


class DBOperationRun(BaseEntity):
    """Audit row for a long-running SSE operation.

    Written at kickoff with ``status="running"`` by the seam-level
    ``OperationRunRecorder``; updated on terminal events with the final
    status, merged ``counts``, and accumulated ``issues``. ``counts`` and
    ``issues`` are JSONB because each operation type defines its own
    payload shape.
    """

    __tablename__: str = "operation_runs"
    __table_args__: tuple[SchemaItem, ...] = (
        Index("ix_operation_runs_user_id_started_at", "user_id", "started_at"),
    )

    user_id: Mapped[str] = mapped_column(
        String(), nullable=False, default="default", server_default="default"
    )
    operation_type: Mapped[str] = mapped_column(String(64), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    counts: Mapped[JsonDict] = mapped_column(
        PgJsonb, nullable=False, server_default=text("'{}'::jsonb"), default=dict
    )
    issues: Mapped[list[JsonDict]] = mapped_column(
        PgJsonb, nullable=False, server_default=text("'[]'::jsonb"), default=list
    )
    # Provenance: the schedule that fired this sync, if any. ON DELETE SET NULL
    # so deleting a schedule preserves its historical runs (migration 026).
    triggered_by_schedule_id: Mapped[UuidType | None] = mapped_column(
        PgUuidCol(as_uuid=True),
        ForeignKey("schedules.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )


class DBSchedule(BaseEntity):
    """A schedule that fires a workflow run or a background sync on a cadence.

    Exactly one target is set: ``workflow_id`` (FK, CASCADE) XOR ``sync_target``
    (a free-text ``"service:entity"`` key validated in the application layer).
    No RLS policy — like ``workflow_runs``, per-user isolation is enforced by the
    repository's ``WHERE user_id`` filter, while the scheduler's cross-tenant poll
    (``find_due_schedules``) reads every user's due rows. The CHECK constraints
    (the exclusive target arc, the cadence ranges, and the non-negative counters)
    live in migration 025 per the codebase convention (CHECKs never in
    ``__table_args__``). ``target_type`` is NOT stored — it is derived on the
    ``Schedule`` entity from whether ``workflow_id`` is set.
    """

    __tablename__: str = "schedules"
    __table_args__: tuple[SchemaItem, ...] = (
        # CRUD + list_for_user scan by owner.
        Index("ix_schedules_user_id", "user_id"),
        # Hot path: the poll query is WHERE status='enabled' AND next_run_at<=now.
        Index("ix_schedules_status_next_run_at", "status", "next_run_at"),
        # Reaper hot path: WHERE started_at IS NOT NULL AND started_at<threshold.
        # Partial — only in-flight claims (a small set) are indexed.
        Index(
            "ix_schedules_started_at",
            "started_at",
            postgresql_where=text("started_at IS NOT NULL"),
        ),
        # One workflow-schedule per (user, workflow), one sync-schedule per
        # (user, sync_target). Partial because the unused arm is NULL for the
        # other target type (mirrors uq_workflow_runs_active's partial idiom).
        Index(
            "uq_schedules_workflow_target",
            "user_id",
            "workflow_id",
            unique=True,
            postgresql_where=text("workflow_id IS NOT NULL"),
        ),
        Index(
            "uq_schedules_sync_target",
            "user_id",
            "sync_target",
            unique=True,
            postgresql_where=text("sync_target IS NOT NULL"),
        ),
    )

    user_id: Mapped[str] = mapped_column(
        String(), nullable=False, default="default", server_default="default"
    )
    # Exclusive arc — exactly one is set (enforced by CHECK in migration 025).
    workflow_id: Mapped[UuidType | None] = mapped_column(
        PgUuidCol(as_uuid=True),
        ForeignKey("workflows.id", ondelete="CASCADE"),
        nullable=True,
    )
    sync_target: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Cadence (minute granularity, local to `timezone`): day_of_week NULL ⇒ daily
    # at hour:minute; set (0=Sun…6=Sat) ⇒ weekly on that day. The 'daily'/'weekly'
    # kind is derived, never stored (range CHECKs live in migration 025).
    hour: Mapped[int] = mapped_column(nullable=False)
    minute: Mapped[int] = mapped_column(nullable=False)
    day_of_week: Mapped[int | None] = mapped_column(nullable=True)
    timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, default="UTC", server_default="UTC"
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="enabled", server_default="enabled"
    )
    # Pre-computed UTC fire time (whole seconds) — the poll index column.
    next_run_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    # Reaper claim marker: non-null while a dispatch is in flight.
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Last-run observability.
    last_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_run_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    # Provenance pointer to the most recent run (workflow_runs.id for a workflow
    # schedule, operation_runs.id for a sync) — no hard FK to avoid a polymorphic
    # constraint; the run tables carry the reverse triggered_by_schedule_id FK.
    last_run_id: Mapped[UuidType | None] = mapped_column(
        PgUuidCol(as_uuid=True), nullable=True
    )
    run_count: Mapped[int] = mapped_column(
        nullable=False, default=0, server_default="0"
    )
    consecutive_failures: Mapped[int] = mapped_column(
        nullable=False, default=0, server_default="0"
    )
