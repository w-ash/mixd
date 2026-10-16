"""The alias cache: alternative names a service states for its own artist.

Keyed on the connector row, not the canonical artist, so MusicBrainz and (later)
Discogs aliases share one table without a polymorphic source column. Nothing
here is user-scoped — like ``connector_artists`` the table is a shared cache.
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_logger
from src.domain.entities.artist import ArtistAlias
from src.domain.matching.artist_equivalence import (
    EMPTY_EQUIVALENCE,
    ArtistEquivalence,
    equivalence_from_alias_rows,
)
from src.infrastructure.persistence.database.models import DBArtistAlias
from src.infrastructure.persistence.repositories.artist.mapper import ArtistAliasMapper
from src.infrastructure.persistence.repositories.base_repo import BaseRepository
from src.infrastructure.persistence.repositories.repo_decorator import db_operation

logger = get_logger(__name__)


class ArtistAliasRepository(BaseRepository[DBArtistAlias, ArtistAlias]):
    """Repository for cached connector-artist aliases."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize with database session and data mapper."""
        super().__init__(
            session=session,
            model_class=DBArtistAlias,
            mapper=ArtistAliasMapper(),
        )

    @db_operation("replace_aliases")
    async def replace_aliases(
        self, connector_artist_id: UUID, aliases: Sequence[ArtistAlias]
    ) -> int:
        """Replace one connector artist's aliases wholesale.

        A refresh replaces rather than merges: MusicBrainz can retire an alias,
        and a merge would keep serving it forever. Returns the number of rows
        the refresh left behind.
        """
        await self.session.execute(
            delete(DBArtistAlias).where(
                DBArtistAlias.connector_artist_id == connector_artist_id
            )
        )
        if not aliases:
            return 0

        now = datetime.now(UTC)
        return await self.bulk_insert_ignore_conflicts(
            [
                {
                    "id": alias.id,
                    "connector_artist_id": connector_artist_id,
                    "name": alias.name,
                    "sort_name": alias.sort_name,
                    "alias_type": alias.alias_type,
                    "locale": alias.locale,
                    "is_primary": alias.is_primary,
                    "fetched_at": alias.fetched_at or now,
                }
                for alias in aliases
            ],
            conflict_keys=["connector_artist_id", "name", "alias_type", "locale"],
        )

    @db_operation("get_aliases_for_connector_artists")
    async def get_aliases_for_connector_artists(
        self, connector_artist_ids: Sequence[UUID]
    ) -> dict[UUID, list[ArtistAlias]]:
        """Aliases for a batch of connector artists, keyed by connector artist id."""
        if not connector_artist_ids:
            return {}
        rows = await self._execute_query(
            select(DBArtistAlias)
            .where(DBArtistAlias.connector_artist_id.in_(connector_artist_ids))
            .order_by(DBArtistAlias.name)
        )
        aliases: dict[UUID, list[ArtistAlias]] = {}
        for row in rows:
            aliases.setdefault(row.connector_artist_id, []).append(
                await self.mapper.to_domain(row)
            )
        return aliases

    @db_operation("find_connector_artist_ids_by_alias")
    async def find_connector_artist_ids_by_alias(
        self, names: Sequence[str]
    ) -> dict[str, list[UUID]]:
        """Connector artists carrying any of these names as an alias.

        Case-insensitive, and keyed by the caller's own spelling so a lookup
        reads back under the string it asked with. Two requested spellings that
        fold together share one entry — the first one wins.
        """
        if not names:
            return {}
        requested: dict[str, str] = {}
        for name in names:
            requested.setdefault(name.lower(), name)

        result = await self.session.execute(
            select(DBArtistAlias.name, DBArtistAlias.connector_artist_id).where(
                func.lower(DBArtistAlias.name).in_(list(requested))
            )
        )
        found: dict[str, list[UUID]] = {}
        for name, connector_artist_id in result.tuples():
            spelling = requested.get(name.lower())
            if spelling is None:
                continue
            found.setdefault(spelling, []).append(connector_artist_id)
        return found

    @db_operation("get_artist_equivalence")
    async def get_artist_equivalence(self, names: Sequence[str]) -> ArtistEquivalence:
        """The alias groups covering a batch's credited artist names.

        One name lookup for the distinct names, then one read of every row
        that states a spelling of the connector artists they reach — other
        artists' rows included, so a spelling two artists share names
        neither. Blank names are dropped; no names, or no cached alias for
        any of them, gives the empty equivalence.
        """
        distinct = sorted({name.strip() for name in names if name.strip()})
        if not distinct:
            return EMPTY_EQUIVALENCE

        ids_by_name = await self.find_connector_artist_ids_by_alias(distinct)
        if not ids_by_name:
            return EMPTY_EQUIVALENCE

        reachable = sorted({
            connector_artist_id
            for ids in ids_by_name.values()
            for connector_artist_id in ids
        })
        return equivalence_from_alias_rows(
            await self._rows_stating_spellings_of(reachable)
        )

    async def _rows_stating_spellings_of(
        self, connector_artist_ids: Sequence[UUID]
    ) -> list[ArtistAlias]:
        """Every alias row whose name one of these artists also states.

        The artists' own rows, plus any other artist's row that states the
        same spelling. Case-insensitive, like the name lookup, so the
        ``lower(name)`` index serves both sides.
        """
        spellings = select(func.lower(DBArtistAlias.name)).where(
            DBArtistAlias.connector_artist_id.in_(connector_artist_ids)
        )
        rows = await self._execute_query(
            select(DBArtistAlias)
            .where(func.lower(DBArtistAlias.name).in_(spellings))
            .order_by(DBArtistAlias.name)
        )
        return [await self.mapper.to_domain(row) for row in rows]


__all__ = ["ArtistAliasRepository"]
