"""Canonical playlists, the connector-playlist cache, links, and membership.

Also the per-link sync bases and the connector-playlist assignments.
"""

from datetime import UTC, datetime

from sqlalchemy import (
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
from src.infrastructure.persistence.database.models.base import (
    BaseEntity,
    PgJsonb,
    PgUuidCol,
    UuidType,
)
from src.infrastructure.persistence.database.models.track import DBTrack


class DBPlaylist(BaseEntity):
    """User playlist metadata.

    Represents user-created playlists with track associations.
    """

    __tablename__: str = "playlists"

    user_id: Mapped[str] = mapped_column(String(), nullable=False, index=True)
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
        UniqueConstraint(
            "connector_name",
            "connector_playlist_identifier",
            name="uq_connector_playlists_connector_name",
        ),
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

    user_id: Mapped[str] = mapped_column(String(), nullable=False)
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
        # FK indexes as migration 014 created them.
        Index("ix_playlist_tracks_playlist_id", "playlist_id"),
        Index("ix_playlist_tracks_track_id", "track_id"),
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

    user_id: Mapped[str] = mapped_column(String(), nullable=False)
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

    user_id: Mapped[str] = mapped_column(String(), nullable=False)
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

    user_id: Mapped[str] = mapped_column(String(), nullable=False)
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
