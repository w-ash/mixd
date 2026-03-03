"""Repository for connector play operations."""

# pyright: reportExplicitAny=false
# Legitimate Any: SQLAlchemy column types, JSON fields

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from src.config import get_logger
from src.domain.entities import ConnectorTrackPlay, ensure_utc
from src.infrastructure.persistence.database.db_models import DBConnectorPlay
from src.infrastructure.persistence.repositories.base_repo import BaseRepository
from src.infrastructure.persistence.repositories.repo_decorator import db_operation

logger = get_logger(__name__)


def _chunked[T](items: list[T], size: int) -> list[list[T]]:
    """Split items into fixed-size chunks."""
    return [items[i : i + size] for i in range(0, len(items), size)]


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
        """Bulk insert connector plays efficiently with deduplication.

        Args:
            connector_plays: List of ConnectorTrackPlay domain objects from API ingestion

        Returns:
            tuple[int, int]: (inserted_count, duplicate_count)
        """
        if not connector_plays:
            return (0, 0)

        logger.info(f"Bulk inserting {len(connector_plays)} connector plays")

        # Find existing plays for deduplication
        existing_plays = await self._find_existing_connector_plays(connector_plays)
        new_plays = self._filter_duplicates(connector_plays, existing_plays)

        duplicate_count = len(connector_plays) - len(new_plays)
        if duplicate_count > 0:
            logger.info(
                f"Filtered out {duplicate_count} duplicate connector plays "
                + f"(inserting {len(new_plays)} new plays)"
            )

        if not new_plays:
            logger.info("All connector plays were duplicates - no new plays to insert")
            return (0, duplicate_count)

        # Prepare data for bulk insert by converting domain objects to db format
        play_data: list[dict[str, Any]] = []
        for play in new_plays:
            # Ensure datetime fields are timezone-aware using utility function
            played_at = ensure_utc(play.played_at)
            import_timestamp = ensure_utc(play.import_timestamp)
            resolved_at = ensure_utc(play.resolved_at)

            # Build raw metadata from ConnectorTrackPlay fields
            raw_metadata = {
                "artist_name": play.artist_name,
                "track_name": play.track_name,
                "album_name": play.album_name,
                "service_metadata": play.service_metadata,
                "api_page": play.api_page,
                **play.raw_data,  # Include any additional raw data
            }

            db_data = {
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
            }
            play_data.append(db_data)

        # Use bulk upsert from BaseRepository
        result = await self.bulk_upsert(
            play_data,
            lookup_keys=[
                "connector_name",
                "connector_track_identifier",
                "played_at",
                "ms_played",
            ],
            return_models=False,
        )

        # Return count of actually inserted records and duplicate count
        logger.info(f"Successfully inserted {result} new connector plays")
        return (result, duplicate_count)

    async def _find_existing_connector_plays(
        self, plays: list[ConnectorTrackPlay]
    ) -> set[tuple[str, str, datetime | None, int | None]]:
        """Find existing connector plays that match the lookup keys.

        Uses batched queries to avoid SQLite expression tree limit.
        """
        if not plays:
            return set()

        existing_keys: set[tuple[str, str, datetime | None, int | None]] = set()

        # Batch plays to avoid SQLite expression tree limit
        batch_size = 200

        for batch in _chunked(plays, batch_size):
            # Build conditions for this batch
            conditions: list[ColumnElement[bool]] = []
            for play in batch:
                condition = (
                    (self.model_class.connector_name == play.connector_name)
                    & (
                        self.model_class.connector_track_identifier
                        == play.connector_track_identifier
                    )
                    & (self.model_class.played_at == play.played_at)
                    & (self.model_class.ms_played == play.ms_played)
                )
                conditions.append(condition)

            # Combine batch conditions with OR
            combined_condition: ColumnElement[bool] = conditions[0]
            for condition in conditions[1:]:
                combined_condition |= condition

            # Query for existing plays in this batch
            query = select(self.model_class).where(combined_condition)
            result = await self.session.execute(query)
            existing_db_plays = result.scalars().all()

            # Convert to set of tuples for fast lookup
            for play in existing_db_plays:
                # Normalize timezone for consistent comparison using utility function
                played_at = ensure_utc(play.played_at)

                existing_keys.add((
                    play.connector_name,
                    play.connector_track_identifier,
                    played_at,
                    play.ms_played,
                ))

        return existing_keys

    def _filter_duplicates(
        self,
        plays: list[ConnectorTrackPlay],
        existing_keys: set[tuple[str, str, datetime | None, int | None]],
    ) -> list[ConnectorTrackPlay]:
        """Filter out plays that already exist in the database."""
        new_plays: list[ConnectorTrackPlay] = []

        for play in plays:
            # Normalize timezone for consistent comparison using utility function
            played_at = ensure_utc(play.played_at)

            play_key = (
                play.connector_name,
                play.connector_track_identifier,
                played_at,
                play.ms_played,
            )

            if play_key not in existing_keys:
                new_plays.append(play)

        return new_plays
