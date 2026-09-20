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
from src.domain.entities.track_mapping import SupersessionReason
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
        rows = self._deduplicate_batch(
            [
                {
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
            ],
            ["connector_name", "connector_artist_identifier"],
            label="bulk_upsert_connector_artists",
        )

        stmt = pg_insert(DBConnectorArtist).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["connector_name", "connector_artist_identifier"],
            set_={
                "name": stmt.excluded.name,
                "raw_metadata": stmt.excluded.raw_metadata,
                "last_updated": stmt.excluded.last_updated,
                "updated_at": now,
            },
        ).returning(DBConnectorArtist)

        result = await self.session.execute(stmt)
        return {
            row.connector_artist_identifier: await self.mapper.to_domain(row)
            for row in result.scalars().all()
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
        self,
        rows: Sequence[Mapping[str, object]],
        *,
        reason: SupersessionReason = "rematch",
    ) -> MappingAssertion:
        """Assert a batch of artist mappings through the generic mechanism."""
        return await self.mapping_repo.assert_mappings(rows, reason=reason)

    async def ensure_primaries(
        self,
        candidates: Sequence[PrimaryCandidate],
        *,
        mode: ElectionMode = "fill",
    ) -> list[PrimaryCandidate]:
        """Elect one primary mapping per (artist, connector) pair."""
        return await self.mapping_repo.ensure_primaries(candidates, mode=mode)

    async def record_assertion(self, assertion: MappingAssertion) -> None:
        """Record the resolution events one assertion earned."""
        await self.mapping_repo.record_assertion(assertion)


__all__ = [
    "ARTIST_MAPPING_SHAPE",
    "ArtistConnectorRepository",
    "ArtistMappingRepository",
    "ConnectorArtistRepository",
]
