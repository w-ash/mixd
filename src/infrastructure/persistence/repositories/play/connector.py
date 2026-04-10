"""Repository for connector play operations."""

from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_logger
from src.domain.entities import ConnectorTrackPlay, ensure_utc
from src.infrastructure.persistence.database.db_models import DBConnectorPlay
from src.infrastructure.persistence.repositories.base_repo import BaseRepository
from src.infrastructure.persistence.repositories.repo_decorator import db_operation

logger = get_logger(__name__)


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
