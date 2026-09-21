"""Connector-artist cache, artist mappings, and the composition over both.

The mapping mechanism — assert, live scoping, election, event recording — is
the generic :class:`MappingRepository`; :class:`ArtistMappingRepository` is its
artist instantiation and adds nothing, because artists keep no state outside
the mapping table that a Core write could leave stale. (``DBArtist.mappings``
is ``lazy="raise_on_sql"`` and nothing in this stack eager-loads it, so the
``_expire_owner_identity`` hook has nothing to expire.)

``artist_mappings`` carries no supersession columns, so a changed decision is
rewritten in place and the resolution event log is the only history.
"""

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_logger
from src.domain.entities.artist import Artist, ArtistMapping, ConnectorArtist
from src.domain.repositories.artist import ArtistMappingInfo
from src.domain.repositories.mapping import ElectionMode, PrimaryCandidate
from src.infrastructure.persistence.database.models import (
    DBArtist,
    DBArtistMapping,
    DBConnectorArtist,
)
from src.infrastructure.persistence.repositories._shared.mapping import (
    MappingAssertion,
    MappingRepository,
    MappingShape,
)
from src.infrastructure.persistence.repositories.artist.mapper import (
    ArtistMapper,
    ArtistMappingMapper,
    ConnectorArtistMapper,
)
from src.infrastructure.persistence.repositories.base_repo import BaseRepository
from src.infrastructure.persistence.repositories.repo_decorator import db_operation

logger = get_logger(__name__)

# The live-identity key — the columns behind ``uq_artist_mappings_user_connector``.
# Plain unique, not partial: with no supersession every row is live.
ARTIST_MAPPING_SHAPE = MappingShape(
    entity_kind="artist",
    owner_id_col="artist_id",
    connector_id_col="connector_artist_id",
    live_key=("user_id", "connector_artist_id"),
    supersession=False,
)


class ArtistMappingRepository(MappingRepository[DBArtistMapping, ArtistMapping]):
    """The artist instantiation of the generic mapping mechanism."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize with database session and data mapper."""
        super().__init__(
            session=session,
            model_class=DBArtistMapping,
            mapper=ArtistMappingMapper(),
            shape=ARTIST_MAPPING_SHAPE,
        )


class ConnectorArtistRepository(BaseRepository[DBConnectorArtist, ConnectorArtist]):
    """The global connector-artist cache: one row per service artist."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize with database session and data mapper."""
        super().__init__(
            session=session,
            model_class=DBConnectorArtist,
            mapper=ConnectorArtistMapper(),
        )

    @db_operation("bulk_upsert_connector_artists")
    async def bulk_upsert_connector_artists(
        self, connector_name: str, artists: Sequence[ConnectorArtist]
    ) -> dict[str, ConnectorArtist]:
        """Upsert a batch of connector artists, keyed by connector identifier.

        A rename is a touch of the existing row, never a second one: the
        service identifier is the identity, so the name, payload and freshness
        stamp are overwritten and the row id — which every mapping references
        — survives.
        """
        if not artists:
            return {}

        now = datetime.now(UTC)
        stored = await self.bulk_upsert(
            [
                {
                    "id": artist.id,
                    "connector_name": connector_name,
                    "connector_artist_identifier": artist.connector_artist_identifier,
                    "name": artist.name,
                    "raw_metadata": artist.raw_metadata,
                    "last_updated": artist.last_updated or now,
                }
                for artist in artists
            ],
            lookup_keys=["connector_name", "connector_artist_identifier"],
        )
        return {row.connector_artist_identifier: row for row in stored}

    @db_operation("find_connector_artists")
    async def find_connector_artists(
        self, connector_name: str, identifiers: Sequence[str]
    ) -> dict[str, ConnectorArtist]:
        """The stored rows for a connector's identifiers, keyed by identifier."""
        if not identifiers:
            return {}
        result = await self.session.execute(
            select(DBConnectorArtist).where(
                DBConnectorArtist.connector_name == connector_name,
                DBConnectorArtist.connector_artist_identifier.in_(list(identifiers)),
            )
        )
        return {
            row.connector_artist_identifier: await ConnectorArtistMapper.to_domain(row)
            for row in result.scalars().all()
        }

    @db_operation("ensure_connector_artists")
    async def ensure_connector_artists(
        self, connector_name: str, artists: Sequence[ConnectorArtist]
    ) -> dict[str, ConnectorArtist]:
        """Insert the absent rows, touch the present ones, return them all.

        The conflict arm moves ``last_updated`` and nothing else, so an
        existing row keeps the service's own spelling and payload. This table
        has no ``user_id``: one tenant inferring an id from a MusicBrainz
        url-rel would otherwise rewrite the record every other tenant reads.

        Submitted rows are deduplicated by identifier first — PostgreSQL
        refuses a DO UPDATE that would touch one row twice in a statement.
        """
        if not artists:
            return {}

        now = datetime.now(UTC)
        rows = {
            artist.connector_artist_identifier: {
                "id": artist.id,
                "connector_name": connector_name,
                "connector_artist_identifier": artist.connector_artist_identifier,
                "name": artist.name,
                "raw_metadata": artist.raw_metadata,
                "last_updated": artist.last_updated or now,
                "created_at": now,
                "updated_at": now,
            }
            for artist in artists
        }

        async with self.session.begin_nested():
            insert = pg_insert(DBConnectorArtist).values(list(rows.values()))
            stored = await self.session.execute(
                insert
                .on_conflict_do_update(
                    constraint="uq_connector_artists_identity",
                    set_={"last_updated": insert.excluded.last_updated},
                )
                .returning(DBConnectorArtist)
                # The caller reads the stored row back. Without this the ORM
                # hands over whatever the identity map already holds, so a row
                # this transaction loaded earlier comes back with a stale
                # ``last_updated`` and the touch looks like it never happened.
                .execution_options(populate_existing=True)
            )
            db_rows = list(stored.scalars().all())

        return {
            row.connector_artist_identifier: await ConnectorArtistMapper.to_domain(row)
            for row in db_rows
        }


class ArtistConnectorRepository:
    """Connects canonical artists with the services that know them.

    Composition over the two repositories the artist identity seam needs: the
    connector-artist cache and the generic mapping mechanism. Its lookups
    return canonical artists through :class:`ArtistMapper` — they join to
    ``artists`` in one statement rather than issuing a second read.
    """

    session: AsyncSession
    connector_repo: ConnectorArtistRepository
    mapping_repo: ArtistMappingRepository

    def __init__(self, session: AsyncSession) -> None:
        """Initialize with database session and dependent repositories."""
        self.session = session
        self.connector_repo = ConnectorArtistRepository(session)
        self.mapping_repo = ArtistMappingRepository(session)

    async def bulk_upsert_connector_artists(
        self, connector_name: str, artists: Sequence[ConnectorArtist]
    ) -> dict[str, ConnectorArtist]:
        """Upsert connector artist records, keyed by connector identifier."""
        return await self.connector_repo.bulk_upsert_connector_artists(
            connector_name, artists
        )

    async def ensure_connector_artists(
        self, connector_name: str, artists: Sequence[ConnectorArtist]
    ) -> dict[str, ConnectorArtist]:
        """Insert the connector artists that are absent, touch the ones that exist."""
        return await self.connector_repo.ensure_connector_artists(
            connector_name, artists
        )

    async def find_connector_artists(
        self, connector_name: str, identifiers: Sequence[str]
    ) -> dict[str, ConnectorArtist]:
        """The stored connector-artist rows for a connector's identifiers."""
        return await self.connector_repo.find_connector_artists(
            connector_name, identifiers
        )

    @db_operation("touch_artist_mappings_last_seen")
    async def touch_last_seen(
        self, connector: str, connector_artist_ids: Sequence[UUID], *, user_id: str
    ) -> None:
        """Stamp ``last_seen_at`` on the mappings of re-encountered connector artists."""
        if not connector_artist_ids:
            return
        _ = await self.session.execute(
            update(DBArtistMapping)
            .where(
                DBArtistMapping.user_id == user_id,
                DBArtistMapping.connector_name == connector,
                DBArtistMapping.connector_artist_id.in_(list(connector_artist_ids)),
            )
            .values(last_seen_at=datetime.now(UTC))
        )

    @db_operation("find_artists_by_connector_artist_ids")
    async def find_artists_by_connector_artist_ids(
        self, connector_artist_ids: Sequence[UUID], *, user_id: str
    ) -> dict[UUID, Artist]:
        """Canonical artists reachable from connector row ids through mappings."""
        if not connector_artist_ids:
            return {}
        result = await self.session.execute(
            select(DBArtistMapping.connector_artist_id, DBArtist)
            .join(DBArtist, DBArtistMapping.artist_id == DBArtist.id)
            .where(
                DBArtistMapping.connector_artist_id.in_(connector_artist_ids),
                DBArtistMapping.user_id == user_id,
                DBArtist.user_id == user_id,
            )
        )
        return {
            connector_artist_id: await ArtistMapper.to_domain(db_artist)
            for connector_artist_id, db_artist in result.tuples()
        }

    @db_operation("get_full_mappings_for_artist")
    async def get_full_mappings_for_artist(
        self, artist_id: UUID, *, user_id: str
    ) -> list[ArtistMappingInfo]:
        """Every mapping on an artist, joined to its connector record."""
        result = await self.session.execute(
            select(
                DBArtistMapping.id,
                DBArtistMapping.connector_name,
                DBConnectorArtist.connector_artist_identifier,
                DBArtistMapping.match_method,
                DBArtistMapping.confidence,
                DBArtistMapping.origin,
                DBArtistMapping.is_primary,
                DBConnectorArtist.name,
                DBConnectorArtist.raw_metadata,
            )
            .join(
                DBConnectorArtist,
                DBArtistMapping.connector_artist_id == DBConnectorArtist.id,
            )
            .where(
                DBArtistMapping.artist_id == artist_id,
                DBArtistMapping.user_id == user_id,
            )
            .order_by(
                DBArtistMapping.is_primary.desc(), DBArtistMapping.confidence.desc()
            )
        )
        return [
            ArtistMappingInfo(
                mapping_id=mapping_id,
                connector_name=connector_name,
                connector_artist_identifier=identifier,
                match_method=match_method,
                confidence=confidence,
                origin=origin,
                is_primary=is_primary,
                name=name,
                raw_metadata=raw_metadata or {},
            )
            for (
                mapping_id,
                connector_name,
                identifier,
                match_method,
                confidence,
                origin,
                is_primary,
                name,
                raw_metadata,
            ) in result.tuples()
        ]

    # ── the generic mapping seam, unwrapped ──────────────────────────

    async def assert_mappings(
        self, rows: Sequence[Mapping[str, object]]
    ) -> MappingAssertion:
        """Assert a batch of artist mappings through the generic mechanism.

        No supersession reason: the artist table has no supersession columns,
        so a changed decision rewrites in place and the reason is never stamped.
        """
        return await self.mapping_repo.assert_mappings(rows)

    async def ensure_primaries(
        self,
        candidates: Sequence[PrimaryCandidate],
        *,
        mode: ElectionMode = "fill",
    ) -> list[PrimaryCandidate]:
        """Elect one primary mapping per (artist, connector) pair."""
        return await self.mapping_repo.ensure_primaries(candidates, mode=mode)

    async def record_assertion(self, assertion: object) -> None:
        """Record the resolution events one assertion earned.

        The protocol hands the assertion around opaquely; only one produced by
        :meth:`assert_mappings` can be recorded.
        """
        if not isinstance(assertion, MappingAssertion):
            raise TypeError(
                f"record_assertion expects a MappingAssertion, got {type(assertion).__name__}"
            )
        await self.mapping_repo.record_assertion(assertion)


__all__ = [
    "ARTIST_MAPPING_SHAPE",
    "ArtistConnectorRepository",
    "ArtistMappingRepository",
    "ConnectorArtistRepository",
]
