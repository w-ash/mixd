"""Repository for connector play operations."""

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import cast
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_logger
from src.domain.entities import ConnectorTrackPlay, ensure_utc
from src.infrastructure.persistence.database.db_models import DBConnectorPlay
from src.infrastructure.persistence.repositories.base_repo import (
    BaseRepository,
    rows_affected,
)
from src.infrastructure.persistence.repositories.repo_decorator import db_operation

logger = get_logger(__name__)

# Bounded VALUES-list size for the resolution write-back UPDATE — a full
# Last.fm history import resolves 50k+ plays in one Phase 2 pass.
_RESOLUTION_BATCH_SIZE = 5_000


class ConnectorTrackPlayRepository(BaseRepository[DBConnectorPlay, ConnectorTrackPlay]):
    """Repository for connector play operations.

    Handles raw play data from external music services before resolution to canonical tracks.
    Uses proper domain entities with mapping to database models.
    """

    session: AsyncSession
    model_class: type[DBConnectorPlay]

    def __init__(self, session: AsyncSession) -> None:
        """Initialize repository with session.

        Does not call super().__init__() because BaseRepository requires a mapper
        that this repository intentionally omits — all mapping is done inline.
        """
        self.session = session
        self.model_class = DBConnectorPlay

    @db_operation("bulk_insert_connector_plays")
    async def bulk_insert_connector_plays(
        self, connector_plays: list[ConnectorTrackPlay]
    ) -> tuple[int, int]:
        """Bulk insert connector plays with ON CONFLICT DO NOTHING deduplication.

        PostgreSQL's unique constraint ``uq_connector_plays_deduplication``
        (connector_name, connector_track_identifier, played_at, ms_played)
        atomically skips duplicates. No pre-query needed.

        Args:
            connector_plays: List of ConnectorTrackPlay domain objects from API ingestion

        Returns:
            tuple[int, int]: (inserted_count, duplicate_count)
        """
        if not connector_plays:
            return (0, 0)

        logger.info(f"Bulk inserting {len(connector_plays)} connector plays")

        # Prepare data for bulk insert by converting domain objects to db format
        play_data: list[dict[str, object]] = []
        for play in connector_plays:
            played_at = ensure_utc(play.played_at)
            import_timestamp = ensure_utc(play.import_timestamp)
            resolved_at = ensure_utc(play.resolved_at)

            raw_metadata = {
                "artist_name": play.artist_name,
                "track_name": play.track_name,
                "album_name": play.album_name,
                "service_metadata": play.service_metadata,
                "api_page": play.api_page,
                **play.raw_data,
            }

            play_data.append({
                # Persist the entity's own id (instead of minting a fresh one
                # via the column default) so first-import ledger rows share the
                # domain entity's id — log/debug correlation.
                "id": play.id,
                "user_id": play.user_id,
                "connector_name": play.connector_name,
                "connector_track_identifier": play.connector_track_identifier,
                "played_at": played_at,
                "ms_played": play.ms_played,
                "raw_metadata": raw_metadata,
                "import_timestamp": import_timestamp,
                "import_source": play.import_source,
                "import_batch_id": play.import_batch_id,
                "resolved_track_id": play.resolved_track_id,
                "resolved_at": resolved_at,
            })

        conflict_keys = [
            "user_id",
            "connector_name",
            "connector_track_identifier",
            "played_at",
            "ms_played",
        ]
        inserted = await self.bulk_insert_ignore_conflicts(play_data, conflict_keys)

        duplicate_count = len(connector_plays) - inserted
        if duplicate_count > 0:
            logger.info(
                f"Skipped {duplicate_count} duplicate connector plays "
                f"(inserted {inserted} new plays)"
            )

        return (inserted, duplicate_count)

    @db_operation("get_resolved_played_at_bounds")
    async def get_resolved_played_at_bounds(
        self, *, user_id: str
    ) -> tuple[datetime, datetime] | None:
        """(min, max) ``played_at`` across a user's resolved ledger rows."""
        stmt = sa.select(
            sa.func.min(DBConnectorPlay.played_at),
            sa.func.max(DBConnectorPlay.played_at),
        ).where(
            DBConnectorPlay.user_id == user_id,
            DBConnectorPlay.resolved_track_id.is_not(None),
        )
        row = (await self.session.execute(stmt)).one()
        low, high = cast("tuple[datetime | None, datetime | None]", tuple(row))
        if low is None or high is None:
            return None
        return (low, high)

    @db_operation("find_resolved_in_window")
    async def find_resolved_in_window(
        self,
        start: datetime,
        end: datetime,
        *,
        user_id: str,
    ) -> list[ConnectorTrackPlay]:
        """Resolved ledger observations with ``played_at`` in [start, end).

        The projection's fetch: window-only, no track filter — the
        resolution-divergence bridge needs observations that resolved to
        *different* canonical tracks but share normalized identity.
        """
        stmt = (
            sa
            .select(DBConnectorPlay)
            .where(
                DBConnectorPlay.user_id == user_id,
                DBConnectorPlay.resolved_track_id.is_not(None),
                DBConnectorPlay.played_at >= start,
                DBConnectorPlay.played_at < end,
            )
            .order_by(DBConnectorPlay.played_at, DBConnectorPlay.id)
            # Resolution lands via Core UPDATE (bulk_update_resolution); any
            # identity-mapped instance must refresh or the projection reads a
            # stale resolved_track_id.
            .execution_options(populate_existing=True)
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return [self._row_to_domain(row) for row in rows]

    @staticmethod
    def _row_to_domain(row: DBConnectorPlay) -> ConnectorTrackPlay:
        """Invert bulk_insert's raw_metadata packing back into the entity.

        ``service_metadata`` must round-trip exactly — the projection's
        context builders read it, and imported vs rebuilt plays must agree.
        The derived ``connector_track_identifier`` recomputes from the same
        inputs that produced the stored value, so it matches.
        """
        raw = dict(row.raw_metadata or {})
        service_metadata = raw.get("service_metadata")
        album_name = raw.get("album_name")
        api_page = raw.get("api_page")
        return ConnectorTrackPlay(
            service=row.connector_name,
            artist_name=str(raw.get("artist_name") or ""),
            track_name=str(raw.get("track_name") or ""),
            album_name=album_name if isinstance(album_name, str) else None,
            played_at=row.played_at,
            ms_played=row.ms_played,
            service_metadata=service_metadata
            if isinstance(service_metadata, Mapping)
            else {},
            api_page=api_page if isinstance(api_page, int) else None,
            user_id=row.user_id,
            import_timestamp=row.import_timestamp,
            import_source=row.import_source,
            import_batch_id=row.import_batch_id,
            resolved_track_id=row.resolved_track_id,
            resolved_at=row.resolved_at,
            id=row.id,
        )

    @db_operation("bulk_update_resolution")
    async def bulk_update_resolution(
        self,
        resolutions: Sequence[tuple[ConnectorTrackPlay, UUID]],
        *,
        resolved_at: datetime,
    ) -> int:
        """Persist canonical resolution onto ledger rows.

        Matches by the ledger natural key rather than entity id: on re-imports
        the stored duplicate keeps its original id while the in-memory entity
        carries a fresh one, so an id-keyed UPDATE would silently miss every
        such row (and could never heal a previously failed resolution).
        ``ms_played`` compares with IS NOT DISTINCT FROM — Last.fm rows are
        NULL there, matching the NULLS NOT DISTINCT dedup constraint.

        Returns:
            Number of ledger rows updated.
        """
        if not resolutions:
            return 0

        total = 0
        for batch_start in range(0, len(resolutions), _RESOLUTION_BATCH_SIZE):
            batch = resolutions[batch_start : batch_start + _RESOLUTION_BATCH_SIZE]
            values_rows = [
                {
                    "user_id": play.user_id,
                    "connector_name": play.connector_name,
                    "connector_track_identifier": play.connector_track_identifier,
                    "played_at": ensure_utc(play.played_at),
                    "ms_played": play.ms_played,
                    "resolved_track_id": track_id,
                }
                for play, track_id in batch
            ]
            resolution_values = sa.values(
                sa.column("user_id", sa.String()),
                sa.column("connector_name", sa.String()),
                sa.column("connector_track_identifier", sa.String()),
                sa.column("played_at", sa.DateTime(timezone=True)),
                sa.column("ms_played", sa.Integer()),
                sa.column("resolved_track_id", PGUUID(as_uuid=True)),
                name="resolution_values",
            ).data([tuple(row.values()) for row in values_rows])

            stmt = (
                sa
                .update(DBConnectorPlay)
                .where(
                    DBConnectorPlay.user_id == resolution_values.c.user_id,
                    DBConnectorPlay.connector_name
                    == resolution_values.c.connector_name,
                    DBConnectorPlay.connector_track_identifier
                    == resolution_values.c.connector_track_identifier,
                    DBConnectorPlay.played_at == resolution_values.c.played_at,
                    # Explicit cast: a batch whose ms_played values are all
                    # NULL gives PG no type to infer for the VALUES column
                    # (bare NULL defaults to text → "integer = text").
                    DBConnectorPlay.ms_played.is_not_distinct_from(
                        sa.cast(resolution_values.c.ms_played, sa.Integer())
                    ),
                )
                .values(
                    resolved_track_id=resolution_values.c.resolved_track_id,
                    resolved_at=resolved_at,
                )
            )
            result = await self.session.execute(stmt)
            total += rows_affected(result)

        logger.info(f"Wrote back resolution for {total} ledger rows")
        return total
