"""Canonical tracks, the connector-track cache, and per-track child rows.

Child rows: metrics, likes, preferences, tags, and their append-only event logs.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING

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
    PgJsonb,
    PgUuidCol,
    UuidType,
)

if TYPE_CHECKING:
    # Cross-aggregate relationship targets. ``mapping`` and ``play`` import this
    # module for their to-one side, so the reverse side cannot import them back.
    # Annotations are deferred (PEP 649), so at runtime SQLAlchemy sees only the
    # class name and resolves it through the shared declarative registry at
    # ``configure_mappers()`` time; the import here exists for the type checker.
    from src.infrastructure.persistence.database.models.mapping import DBTrackMapping
    from src.infrastructure.persistence.database.models.play import (
        DBConnectorPlay,
        DBTrackPlay,
    )


class DBTrack(BaseEntity):
    """Core track entity with essential metadata.

    Represents the user's music library with plays, likes, and playlist associations.
    Uses hard deletes for simplicity and performance.
    """

    __tablename__: str = "tracks"

    user_id: Mapped[str] = mapped_column(String(), nullable=False)
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

    # Play aggregates, written only by ``recompute_track_play_aggregates`` —
    # never by save_track/to_db, whose full-column write would clobber them.
    play_count: Mapped[int] = mapped_column(
        nullable=False, default=0, server_default="0"
    )
    last_played_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_played_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

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
        # Canonical Reuse normalized fuzzy lookup, and its parenthetical-
        # stripped fallback. ``user_id`` leads because every read of these
        # columns is user-scoped (explicit predicate plus the RLS qual), so
        # without it the probe's cost grew with the whole table.
        Index(
            "ix_tracks_user_normalized_lookup",
            "user_id",
            "title_normalized",
            "artist_normalized",
        ),
        Index(
            "ix_tracks_user_stripped_lookup",
            "user_id",
            "title_stripped",
            "artist_normalized",
        ),
        # Library sort indexes (053). Every listing is
        # ``WHERE user_id = :u ORDER BY <col> <dir> [NULLS LAST], id <dir>``,
        # so each index is (user_id, sort col, id) — `id` last because the
        # keyset predicate compares `(col, id)` as a row and `play_count` has
        # tie groups in the tens of thousands.
        #
        # NOT NULL columns need one index: a backward scan serves DESC.
        Index("ix_tracks_user_title_id", "user_id", "title", "id"),
        Index("ix_tracks_user_created_at_id", "user_id", "created_at", "id"),
        Index("ix_tracks_user_play_count_id", "user_id", "play_count", "id"),
        # Nullable columns need one index per direction, each led by the
        # NULL-ness boolean the repository puts first in its ORDER BY.
        # `(col IS NULL) ASC` / `(col IS NOT NULL) DESC` both sort NULLs last;
        # leading with them keeps every key in one direction, which is what
        # lets the keyset row-comparison seek rather than filter.
        Index(
            "ix_tracks_user_duration_id",
            "user_id",
            text("(duration_ms IS NULL)"),
            "duration_ms",
            "id",
        ),
        Index(
            "ix_tracks_user_duration_desc_id",
            "user_id",
            text("(duration_ms IS NOT NULL) DESC"),
            text("duration_ms DESC"),
            text("id DESC"),
        ),
        Index(
            "ix_tracks_user_last_played_id",
            "user_id",
            text("(last_played_at IS NULL)"),
            "last_played_at",
            "id",
        ),
        Index(
            "ix_tracks_user_last_played_desc_id",
            "user_id",
            text("(last_played_at IS NOT NULL) DESC"),
            text("last_played_at DESC"),
            text("id DESC"),
        ),
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
        UniqueConstraint(
            "connector_name",
            "connector_track_identifier",
            name="uq_connector_tracks_connector_name",
        ),
        Index(None, "connector_name", "isrc"),
    )


class DBTrackMetric(BaseEntity):
    """Time-series metrics for tracks from external services.

    Stores track performance metrics (play counts, listener counts) for analytics and trends.
    """

    __tablename__: str = "track_metrics"
    __table_args__: tuple[SchemaItem, ...] = (
        # The unique index doubles as the lookup index.
        UniqueConstraint(
            "track_id",
            "connector_name",
            "metric_type",
            name="uq_track_metrics_track_id",
        ),
    )

    user_id: Mapped[str] = mapped_column(String(), nullable=False)
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
    """A track liked on one service.

    Presence row: it exists while the track is liked on the service and is
    deleted when it is not. There is no status flag or tombstone.
    """

    __tablename__: str = "track_likes"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint(
            "user_id", "track_id", "service", name="uq_track_likes_user_track_service"
        ),
    )

    user_id: Mapped[str] = mapped_column(String(), nullable=False)
    track_id: Mapped[UuidType] = mapped_column(
        PgUuidCol(as_uuid=True), ForeignKey("tracks.id", ondelete="CASCADE")
    )
    service: Mapped[str] = mapped_column(String(32))  # 'spotify', 'lastfm', 'mixd'
    liked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Relationships
    track: Mapped[DBTrack] = relationship(
        back_populates="likes",
        passive_deletes=True,
        lazy="raise_on_sql",
    )


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

    user_id: Mapped[str] = mapped_column(String(), nullable=False)
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

    user_id: Mapped[str] = mapped_column(String(), nullable=False)
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
        UniqueConstraint(
            "user_id", "track_id", "tag", name="uq_track_tags_user_id_track_id_tag"
        ),
        Index("ix_track_tags_user_id_tag", "user_id", "tag"),
        Index("ix_track_tags_user_id_namespace", "user_id", "namespace"),
        Index("ix_track_tags_user_id_tagged_at", "user_id", "tagged_at"),
    )

    user_id: Mapped[str] = mapped_column(String(), nullable=False)
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

    user_id: Mapped[str] = mapped_column(String(), nullable=False)
    track_id: Mapped[UuidType] = mapped_column(
        PgUuidCol(as_uuid=True), ForeignKey("tracks.id", ondelete="CASCADE")
    )
    tag: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(8))  # add, remove
    source: Mapped[str] = mapped_column(String(32))
    tagged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
