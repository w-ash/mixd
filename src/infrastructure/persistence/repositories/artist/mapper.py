"""Artist mappers: pure functions of the loaded row, no session, no writes.

One mapper per table the artist stack reads back as a domain entity. They
mirror ``track/mapper.py`` in shape but are all straight column reads — an
artist's identity lives in its mappings, so nothing here has to reconstruct a
display identifier from a relationship.
"""

from typing import cast, override

from attrs import define

from src.domain.entities import ensure_utc
from src.domain.entities.artist import (
    Artist,
    ArtistAlias,
    ArtistMapping,
    ConnectorArtist,
    is_artist_kind,
)
from src.domain.entities.track_mapping import is_mapping_origin, is_match_method
from src.infrastructure.persistence.database.models import (
    DBArtist,
    DBArtistAlias,
    DBArtistMapping,
    DBConnectorArtist,
)
from src.infrastructure.persistence.repositories.mappers import (
    BaseModelMapper,
)


@define(frozen=True, slots=True)
class ArtistMapper(BaseModelMapper[DBArtist, Artist]):
    """Bidirectional mapper for the canonical artist row."""

    @override
    @staticmethod
    async def to_domain(db_model: DBArtist) -> Artist:
        """Convert a database artist to the domain entity.

        ``kind`` is narrowed at the boundary: a value the domain vocabulary no
        longer knows reads as ``None`` rather than propagating a string the
        type says cannot exist.
        """
        kind = db_model.kind
        return Artist(
            name=db_model.name,
            user_id=db_model.user_id,
            mbid=db_model.mbid,
            kind=kind if kind is not None and is_artist_kind(kind) else None,
            created_at=ensure_utc(db_model.created_at),
            updated_at=ensure_utc(db_model.updated_at),
            id=db_model.id,
        )

    @override
    @staticmethod
    def to_db(domain_model: Artist) -> DBArtist:
        """Convert a domain artist to the database model."""
        return DBArtist(
            id=domain_model.id,
            user_id=domain_model.user_id,
            name=domain_model.name,
            mbid=domain_model.mbid,
            kind=domain_model.kind,
        )


@define(frozen=True, slots=True)
class ConnectorArtistMapper(BaseModelMapper[DBConnectorArtist, ConnectorArtist]):
    """Bidirectional mapper for the global connector-artist cache row."""

    @override
    @staticmethod
    async def to_domain(db_model: DBConnectorArtist) -> ConnectorArtist:
        """Convert a database connector artist to the domain entity."""
        return ConnectorArtist(
            connector_name=db_model.connector_name,
            connector_artist_identifier=db_model.connector_artist_identifier,
            name=db_model.name,
            raw_metadata=db_model.raw_metadata or {},
            last_updated=ensure_utc(db_model.last_updated),
            id=db_model.id,
        )

    @override
    @staticmethod
    def to_db(domain_model: ConnectorArtist) -> DBConnectorArtist:
        """Convert a domain connector artist to the database model."""
        return DBConnectorArtist(
            id=domain_model.id,
            connector_name=domain_model.connector_name,
            connector_artist_identifier=domain_model.connector_artist_identifier,
            name=domain_model.name,
            raw_metadata=domain_model.raw_metadata,
            last_updated=domain_model.last_updated,
        )


@define(frozen=True, slots=True)
class ArtistMappingMapper(BaseModelMapper[DBArtistMapping, ArtistMapping]):
    """Bidirectional mapper for one artist mapping.

    No supersession fields: the table carries none, so a changed decision is a
    rewrite and the event log is the history.
    """

    @override
    @staticmethod
    async def to_domain(db_model: DBArtistMapping) -> ArtistMapping:
        """Convert a database artist mapping to the domain entity."""
        method = db_model.match_method
        origin = db_model.origin
        # One policy for every reader, as on ``track_mappings``: a row outside
        # the vocabulary is a schema fact nobody designed, so it raises rather
        # than being quietly normalised into a value the row does not hold.
        if not is_match_method(method):
            raise ValueError(
                f"artist_mapping {db_model.id} carries a match_method outside "
                f"the domain vocabulary: {method!r}"
            )
        if not is_mapping_origin(origin):
            raise ValueError(
                f"artist_mapping {db_model.id} carries an origin outside the "
                f"domain vocabulary: {origin!r}"
            )
        return ArtistMapping(
            user_id=db_model.user_id,
            artist_id=db_model.artist_id,
            connector_artist_id=db_model.connector_artist_id,
            connector_name=db_model.connector_name,
            match_method=method,
            confidence=db_model.confidence,
            # Widens from ``JsonDict`` to ``dict[str, object]``; the entity is
            # frozen, so the cast avoids a copy without a mutation risk.
            confidence_evidence=cast(
                "dict[str, object] | None", db_model.confidence_evidence
            ),
            origin=origin,
            is_primary=db_model.is_primary,
            last_seen_at=ensure_utc(db_model.last_seen_at),
            created_at=ensure_utc(db_model.created_at),
            updated_at=ensure_utc(db_model.updated_at),
            id=db_model.id,
        )

    @override
    @staticmethod
    def to_db(domain_model: ArtistMapping) -> DBArtistMapping:
        """Convert a domain artist mapping to the database model."""
        return DBArtistMapping(
            id=domain_model.id,
            user_id=domain_model.user_id,
            artist_id=domain_model.artist_id,
            connector_artist_id=domain_model.connector_artist_id,
            connector_name=domain_model.connector_name,
            match_method=domain_model.match_method,
            confidence=domain_model.confidence,
            confidence_evidence=domain_model.confidence_evidence,
            origin=domain_model.origin,
            is_primary=domain_model.is_primary,
            last_seen_at=domain_model.last_seen_at,
        )


@define(frozen=True, slots=True)
class ArtistAliasMapper(BaseModelMapper[DBArtistAlias, ArtistAlias]):
    """Bidirectional mapper for one cached alias."""

    @override
    @staticmethod
    async def to_domain(db_model: DBArtistAlias) -> ArtistAlias:
        """Convert a database alias to the domain entity."""
        return ArtistAlias(
            connector_artist_id=db_model.connector_artist_id,
            name=db_model.name,
            sort_name=db_model.sort_name,
            alias_type=db_model.alias_type,
            locale=db_model.locale,
            is_primary=db_model.is_primary,
            fetched_at=ensure_utc(db_model.fetched_at),
            id=db_model.id,
        )

    @override
    @staticmethod
    def to_db(domain_model: ArtistAlias) -> DBArtistAlias:
        """Convert a domain alias to the database model."""
        return DBArtistAlias(
            id=domain_model.id,
            connector_artist_id=domain_model.connector_artist_id,
            name=domain_model.name,
            sort_name=domain_model.sort_name,
            alias_type=domain_model.alias_type,
            locale=domain_model.locale,
            is_primary=domain_model.is_primary,
            fetched_at=domain_model.fetched_at,
        )


# Straight 1:1 field copy in both directions — the composite primary key
# changes nothing for the mapper, which never reads ``id``.


__all__ = [
    "ArtistAliasMapper",
    "ArtistMapper",
    "ArtistMappingMapper",
    "ConnectorArtistMapper",
]
