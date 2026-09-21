"""Canonical artists, the connector-artist cache, credits, mappings and favorites.

Mirrors the track stack one level up: ``artists`` is the per-user canonical,
``connector_artists`` the global service-record cache (no ``user_id``, no RLS,
like ``connector_tracks``), and ``artist_mappings`` the per-user statement
joining them.

``artist_mappings`` carries no supersession columns: artist ids are treated as
permanent, so a changed decision rewrites the row and the event log is the only
history. The generic mapping repository reads that off its ``MappingShape``,
so the capability can be switched on later as columns, not code.

This module references ``tracks`` and ``connector_tracks`` by name and imports
nothing but ``base`` — ``track`` names :class:`DBTrackArtist` and
:class:`DBConnectorTrackArtist` only under ``TYPE_CHECKING`` for its view-only
credit collections.
"""

from datetime import datetime
from typing import cast

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

from src.domain.entities.artist import ARTIST_KINDS
from src.domain.entities.shared import JsonDict
from src.domain.entities.track_mapping import MAPPING_ORIGINS, MATCH_METHODS
from src.infrastructure.persistence.database.models.base import (
    BaseEntity,
    DatabaseModel,
    PgJsonb,
    PgUuidCol,
    TimestampMixin,
    UuidType,
    vocabulary_check,
)


class DBArtist(BaseEntity):
    """A canonical artist in one user's library.

    Neither ``name`` nor ``mbid`` is unique. Same-name artists are real, and an
    external identifier is a reference, never a key: a MusicBrainz merge can
    leave two canonical artists on one MBID, and a unique constraint there
    would abort the alias refresh instead of letting the planner dedupe.
    Identity uniqueness lives in ``artist_mappings.(user_id,
    connector_artist_id)``.
    """

    __tablename__: str = "artists"

    user_id: Mapped[str] = mapped_column(String(), nullable=False)
    name: Mapped[str] = mapped_column(String(), nullable=False)
    mbid: Mapped[str | None] = mapped_column(String(64))
    # person / group / other, from MusicBrainz ``type``.
    kind: Mapped[str | None] = mapped_column(String(16))

    mappings: Mapped[list[DBArtistMapping]] = relationship(
        back_populates="artist",
        passive_deletes=True,
        lazy="raise_on_sql",
    )
    favorites: Mapped[list[DBArtistFavorite]] = relationship(
        back_populates="artist",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise_on_sql",
    )

    # NOTE: the pg_trgm GIN index on ``name`` (``ix_artists_name_trgm``) is
    # created only via Alembic migration 060 — it requires the pg_trgm
    # extension and would fail under ``metadata.create_all`` in test fixtures.
    __table_args__: tuple[SchemaItem, ...] = (
        CheckConstraint(
            vocabulary_check("kind", ARTIST_KINDS),
            name="kind_vocabulary",
        ),
        # Name lookup is always user-scoped (explicit predicate plus the RLS
        # qual), so ``user_id`` leads.
        Index("ix_artists_user_name", "user_id", "name"),
        # Non-unique by design: the MBID is a reference, not a key.
        Index("ix_artists_mbid", "mbid"),
        Index("ix_artists_user_updated_at", "user_id", "updated_at"),
    )


class DBConnectorArtist(BaseEntity):
    """An artist record as one service states it.

    Global shared cache: no ``user_id`` and no RLS, exactly like
    ``connector_tracks``. ``name`` holds the service spelling with Discogs'
    numeric disambiguation suffix ("Tycho (3)") stripped into ``raw_metadata``
    rather than left in the name.
    """

    __tablename__: str = "connector_artists"

    connector_name: Mapped[str] = mapped_column(String(32), nullable=False)
    connector_artist_identifier: Mapped[str] = mapped_column(String(), nullable=False)
    name: Mapped[str] = mapped_column(String(), nullable=False)
    raw_metadata: Mapped[JsonDict] = mapped_column(PgJsonb, nullable=False)
    last_updated: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    mappings: Mapped[list[DBArtistMapping]] = relationship(
        passive_deletes=True,
        lazy="raise_on_sql",
    )
    aliases: Mapped[list[DBArtistAlias]] = relationship(
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise_on_sql",
    )

    # NOTE: the JSONB GIN index on ``raw_metadata``
    # (``ix_connector_artists_raw_metadata_gin``) is created only via Alembic
    # migration 060 — ``metadata.create_all`` does not build it.
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint(
            "connector_name",
            "connector_artist_identifier",
            name="uq_connector_artists_identity",
        ),
        Index("ix_connector_artists_name", "name"),
    )


class DBArtistMapping(BaseEntity):
    """Maps a connector artist to a canonical artist with confidence scoring.

    Live rows only — no supersession columns. Several rows per service per
    artist are normal rather than an anomaly (alias projects carry separate
    ids on every service and are linked, never merged); ``is_primary`` picks
    the row the UI shows.

    MUST mirror migration 060 exactly: integration tests build the schema with
    ``metadata.create_all``, not the migration chain, so any divergence means
    every integration test runs against a schema production never has.
    """

    __tablename__: str = "artist_mappings"

    user_id: Mapped[str] = mapped_column(String(), nullable=False)
    # CASCADE, not RESTRICT as on ``track_mappings``: without supersession
    # these rows are current identity, not append-only history, so a deleted
    # artist has no history to strand.
    artist_id: Mapped[UuidType] = mapped_column(
        PgUuidCol(as_uuid=True), ForeignKey("artists.id", ondelete="CASCADE")
    )
    # RESTRICT: the connector row is a shared cache entry other tenants' live
    # mappings may name.
    connector_artist_id: Mapped[UuidType] = mapped_column(
        PgUuidCol(as_uuid=True),
        ForeignKey("connector_artists.id", ondelete="RESTRICT"),
    )
    connector_name: Mapped[str] = mapped_column(String(32), nullable=False)
    match_method: Mapped[str] = mapped_column(String(32))
    confidence: Mapped[int]
    confidence_evidence: Mapped[JsonDict | None] = mapped_column(PgJsonb)
    origin: Mapped[str] = mapped_column(
        String(20), nullable=False, default="automatic", server_default="automatic"
    )
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Freshness signal, deliberately NOT evidence: re-encounter proves the
    # connector artist exists, not that the canonical match is right.
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    artist: Mapped[DBArtist] = relationship(
        back_populates="mappings",
        passive_deletes=True,
        lazy="raise_on_sql",
    )
    __table_args__: tuple[SchemaItem, ...] = (
        # The live key the generic assert conflicts on. A plain unique
        # constraint, not a partial index: with no supersession every row is
        # live.
        UniqueConstraint(
            "user_id",
            "connector_artist_id",
            name="uq_artist_mappings_user_connector",
        ),
        # One primary per user-artist-connector triple.
        Index(
            "uq_artist_mappings_primary",
            "user_id",
            "artist_id",
            "connector_name",
            unique=True,
            postgresql_where=text("is_primary = TRUE"),
        ),
        # Storage-boundary enforcement of the domain vocabularies. Growing
        # either means a new migration that drops and recreates the
        # constraint — the Literal alias alone does not migrate the database.
        CheckConstraint(
            vocabulary_check("match_method", MATCH_METHODS),
            name="match_method_vocabulary",
        ),
        CheckConstraint(
            vocabulary_check("origin", MAPPING_ORIGINS),
            name="origin_vocabulary",
        ),
        Index("ix_artist_mappings_artist_lookup", "user_id", "artist_id"),
        Index("ix_artist_mappings_connector_name", "connector_name"),
    )


class DBArtistFavorite(DatabaseModel, TimestampMixin):
    """An artist the user favorited.

    Presence row: it exists while the artist is favorited and is deleted when
    it is not. Mixd-only curation — no ``service`` column and no state flag,
    because a favorite never syncs to a service and so has nothing to push.

    The pair *is* the key, so this is the one table with a composite primary
    key; ``id`` from :class:`DatabaseModel` is cancelled rather than carried as
    a surrogate nothing references.
    """

    __tablename__: str = "artist_favorites"

    # Cancels the surrogate primary key inherited from ``DatabaseModel``:
    # declarative drops a column whose subclass attribute is ``None``. The
    # cast states that to the type checker, which otherwise reads the
    # cancellation as an assignment of the wrong type.
    id = cast("Mapped[UuidType]", None)

    user_id: Mapped[str] = mapped_column(String(), primary_key=True)
    artist_id: Mapped[UuidType] = mapped_column(
        PgUuidCol(as_uuid=True),
        ForeignKey("artists.id", ondelete="CASCADE"),
        primary_key=True,
    )
    favorited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    artist: Mapped[DBArtist] = relationship(
        back_populates="favorites",
        passive_deletes=True,
        lazy="raise_on_sql",
    )


class DBTrackArtist(BaseEntity):
    """One artist credit on one track, at one position.

    An association object in infrastructure only: writes go through Core
    ``ON CONFLICT`` from the canonical row-builder, so there is no writable
    ``relationship()`` on either side — ``DBTrack.artist_credits`` is
    ``viewonly``.

    ``artist_id`` is nullable and ``ON DELETE SET NULL`` on purpose. A Various
    Artists credit never gets an artist row (the sentinel is an album-level
    flag, not a favouritable entity) and a not-yet-resolved credit has not
    been minted one, so the credit must survive without it.
    """

    __tablename__: str = "track_artists"

    user_id: Mapped[str] = mapped_column(String(), nullable=False)
    track_id: Mapped[UuidType] = mapped_column(
        PgUuidCol(as_uuid=True), ForeignKey("tracks.id", ondelete="CASCADE")
    )
    artist_id: Mapped[UuidType | None] = mapped_column(
        PgUuidCol(as_uuid=True), ForeignKey("artists.id", ondelete="SET NULL")
    )
    # Zero-based; the credit order on the record.
    position: Mapped[int] = mapped_column(nullable=False)
    credited_name: Mapped[str] = mapped_column(String(), nullable=False)
    # " & " / " feat. " between this credit and the next, when a source states
    # one. The JSONB backfill has none to give.
    join_phrase: Mapped[str | None] = mapped_column(String())
    # Only when a source states it (Discogs ``extraartists.role``, MusicBrainz
    # relationships). Spotify's structured credits cannot tell a featured
    # artist from a remixer.
    role: Mapped[str | None] = mapped_column(String())

    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint(
            "track_id", "position", name="uq_track_artists_track_position"
        ),
        CheckConstraint("position >= 0", name="position_nonneg"),
        # The SET NULL FK needs its own index for the delete-time probe.
        Index("ix_track_artists_artist", "artist_id"),
        # "All tracks by artist X", which is always user-scoped.
        Index("ix_track_artists_user_artist", "user_id", "artist_id"),
    )


class DBConnectorTrackArtist(BaseEntity):
    """One artist credit on one connector track, at one position.

    The connector twin of :class:`DBTrackArtist`: a global record (no
    ``user_id``, no RLS, like ``connector_tracks``) of what a service says
    about a track's line-up, pointing at the service's own artist record.

    ``connector_artist_id`` is nullable and ``ON DELETE SET NULL``: a credit
    whose service gave no artist id (Apple's song payload) has no record to
    point at and keeps its credited name. Writes go through Core
    ``ON CONFLICT`` from the connector row-builder, so there is no writable
    ``relationship()`` on the track side — ``DBConnectorTrack.artist_credits``
    is ``viewonly``.
    """

    __tablename__: str = "connector_track_artists"

    connector_track_id: Mapped[UuidType] = mapped_column(
        PgUuidCol(as_uuid=True),
        ForeignKey("connector_tracks.id", ondelete="CASCADE"),
    )
    connector_artist_id: Mapped[UuidType | None] = mapped_column(
        PgUuidCol(as_uuid=True),
        ForeignKey("connector_artists.id", ondelete="SET NULL"),
    )
    # Zero-based; the credit order on the service's record.
    position: Mapped[int] = mapped_column(nullable=False)
    credited_name: Mapped[str] = mapped_column(String(), nullable=False)
    join_phrase: Mapped[str | None] = mapped_column(String())
    role: Mapped[str | None] = mapped_column(String())

    # Many-to-one; the mapper reads the service identifier through it.
    connector_artist: Mapped[DBConnectorArtist | None] = relationship(
        lazy="raise_on_sql",
    )

    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint(
            "connector_track_id",
            "position",
            name="uq_connector_track_artists_track_position",
        ),
        CheckConstraint("position >= 0", name="position_nonneg"),
        # The SET NULL FK needs its own index for the delete-time probe.
        Index("ix_connector_track_artists_artist", "connector_artist_id"),
    )


class DBArtistAlias(BaseEntity):
    """An alternative name one service states for a connector artist.

    Hangs off the connector record, not the canonical artist: an alias is the
    service's claim about its own entity. MusicBrainz supplies ``alias_type``
    and ``locale``; Discogs' ``namevariations`` supply neither, hence the
    ``NULLS NOT DISTINCT`` identity constraint — without it two untyped
    aliases of the same name would both insert.
    """

    __tablename__: str = "artist_aliases"

    connector_artist_id: Mapped[UuidType] = mapped_column(
        PgUuidCol(as_uuid=True),
        ForeignKey("connector_artists.id", ondelete="CASCADE"),
    )
    name: Mapped[str] = mapped_column(String(), nullable=False)
    sort_name: Mapped[str | None] = mapped_column(String())
    alias_type: Mapped[str | None] = mapped_column(String(32))
    locale: Mapped[str | None] = mapped_column(String(16))
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint(
            "connector_artist_id",
            "name",
            "alias_type",
            "locale",
            name="uq_artist_aliases_identity",
            postgresql_nulls_not_distinct=True,
        ),
        # The name lookup index is functional (``lower(name)``) and lives in
        # migration 060 only.
    )
