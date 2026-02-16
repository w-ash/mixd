"""Repository for connector play operations."""

from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from toolz import partition_all

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

    def __init__(self, session: AsyncSession) -> None:
        """Initialize repository with session."""
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
                f"(inserting {len(new_plays)} new plays)"
            )

        if not new_plays:
            logger.info("All connector plays were duplicates - no new plays to insert")
            return (0, duplicate_count)

        # Prepare data for bulk insert by converting domain objects to db format
        play_data = []
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
        inserted_count = len(play_data) if isinstance(result, list) else result
        logger.info(f"Successfully inserted {inserted_count} new connector plays")
        return (inserted_count, duplicate_count)

    @db_operation("get_unresolved_connector_plays")
    async def get_unresolved_connector_plays(
        self,
        connector: str | None = None,
        limit: int | None = None,
    ) -> list[ConnectorTrackPlay]:
        """Get connector plays that haven't been resolved to canonical tracks yet.

        Args:
            connector: Optional connector name to filter by
            limit: Optional limit on number of plays to return

        Returns:
            List of unresolved ConnectorTrackPlay domain objects ordered by played_at
        """
        # Build query for unresolved plays
        query = select(self.model_class).where(
            self.model_class.resolved_track_id.is_(None)
        )

        if connector:
            query = query.where(self.model_class.connector_name == connector)

        # Order by played_at for consistent processing
        query = query.order_by(self.model_class.played_at)

        if limit:
            query = query.limit(limit)

        result = await self.session.execute(query)
        db_plays = result.scalars().all()

        # Convert to ConnectorTrackPlay domain objects
        connector_plays = []
        for db_play in db_plays:
            # Extract fields from raw_metadata
            raw_metadata = db_play.raw_metadata or {}
            artist_name = raw_metadata.get("artist_name", "Unknown")
            track_name = raw_metadata.get("track_name", "Unknown")
            album_name = raw_metadata.get("album_name")
            service_metadata = raw_metadata.get("service_metadata", {})
            api_page = raw_metadata.get("api_page")

            # Create ConnectorTrackPlay from database record
            connector_play = ConnectorTrackPlay(
                artist_name=artist_name,
                track_name=track_name,
                played_at=db_play.played_at,
                service=db_play.connector_name,
                album_name=album_name,
                ms_played=db_play.ms_played,
                service_metadata=service_metadata,
                api_page=api_page,
                raw_data={
                    k: v
                    for k, v in raw_metadata.items()
                    if k
                    not in [
                        "artist_name",
                        "track_name",
                        "album_name",
                        "service_metadata",
                        "api_page",
                    ]
                },
                import_timestamp=db_play.import_timestamp,
                import_source=db_play.import_source,
                import_batch_id=db_play.import_batch_id,
                resolved_track_id=db_play.resolved_track_id,
                resolved_at=db_play.resolved_at,
                id=db_play.id,
            )
            connector_plays.append(connector_play)

        logger.debug(f"Found {len(connector_plays)} unresolved connector plays")
        return connector_plays

    @db_operation("mark_plays_resolved")
    async def mark_plays_resolved(
        self,
        connector_play_ids: list[int],
        resolved_track_id: int,
    ) -> int:
        """Mark connector plays as resolved to a canonical track.

        Args:
            connector_play_ids: List of connector play database IDs
            resolved_track_id: Canonical track ID they resolve to

        Returns:
            Number of connector plays successfully marked as resolved
        """
        if not connector_play_ids:
            return 0

        # Update resolved plays in bulk
        stmt = (
            update(self.model_class)
            .where(self.model_class.id.in_(connector_play_ids))
            .values(
                resolved_track_id=resolved_track_id,
                resolved_at=datetime.now(UTC),
            )
        )

        result = await self.session.execute(stmt)
        updated_count = result.rowcount

        logger.info(
            f"Marked {updated_count} connector plays as resolved to track {resolved_track_id}"
        )
        return updated_count

    async def _find_existing_connector_plays(
        self, plays: list[ConnectorTrackPlay]
    ) -> set[tuple]:
        """Find existing connector plays that match the lookup keys.

        Uses batched queries to avoid SQLite expression tree limit.
        """
        if not plays:
            return set()

        existing_keys = set()

        # Batch plays to avoid SQLite expression tree limit
        batch_size = 200

        for batch_tuple in partition_all(batch_size, plays):
            batch = list(batch_tuple)  # toolz preserves original types

            # Build conditions for this batch
            conditions = []
            for play in batch:
                # Type checker has inference issues with partition_all, but runtime types are correct
                play_typed: ConnectorTrackPlay = play  # type: ignore[assignment]
                condition = (
                    (self.model_class.connector_name == play_typed.connector_name)  # type: ignore[attr-defined]
                    & (
                        self.model_class.connector_track_identifier
                        == play_typed.connector_track_identifier
                    )  # type: ignore[attr-defined]
                    & (self.model_class.played_at == play_typed.played_at)  # type: ignore[attr-defined]
                    & (self.model_class.ms_played == play_typed.ms_played)  # type: ignore[attr-defined]
                )
                conditions.append(condition)

            # Combine batch conditions with OR
            if len(conditions) == 1:
                combined_condition = conditions[0]
            else:
                combined_condition = conditions[0]
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
        self, plays: list[ConnectorTrackPlay], existing_keys: set[tuple]
    ) -> list[ConnectorTrackPlay]:
        """Filter out plays that already exist in the database."""
        new_plays = []

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
