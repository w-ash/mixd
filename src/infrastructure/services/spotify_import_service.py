"""Spotify play history import service following clean architecture patterns.

Imports play data from Spotify data export JSON files using the BasePlayImporter
template method pattern for consistency with other music service imports.
"""

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from src.config import get_config, get_logger
from src.domain.entities import OperationResult, TrackPlay
from src.domain.repositories.interfaces import (
    ConnectorRepositoryProtocol,
    PlaysRepositoryProtocol,
    TrackRepositoryProtocol,
)
from src.infrastructure.adapters.spotify_play_adapter import SpotifyPlayAdapter
from src.infrastructure.services.base_play_importer import BasePlayImporter

logger = get_logger(__name__)


class SpotifyImportService(BasePlayImporter):
    """Imports Spotify play data from JSON export files using template method pattern.

    Follows the same clean architecture pattern as LastfmPlayImporter, extending
    BasePlayImporter to provide consistent workflow, error handling, and progress tracking.
    """

    def __init__(
        self,
        plays_repository: PlaysRepositoryProtocol,
        track_repository: TrackRepositoryProtocol,
        connector_repository: ConnectorRepositoryProtocol,
        spotify_adapter: SpotifyPlayAdapter | None = None,
    ) -> None:
        """Initialize Spotify import service with required repositories."""
        super().__init__(plays_repository)
        self.operation_name = "Spotify Import"
        self.track_repository = track_repository
        self.connector_repository = connector_repository
        self.spotify_adapter = spotify_adapter or SpotifyPlayAdapter()

    async def import_from_file(
        self,
        file_path: Path,
        import_batch_id: str | None = None,
        progress_callback: Callable[[int, int, str], None] | None = None,
        uow: Any | None = None,
    ) -> OperationResult:
        """Import play data from Spotify JSON export file.

        Args:
            file_path: Path to Spotify data export JSON file
            import_batch_id: Optional batch ID for tracking related imports
            progress_callback: Optional callback for progress updates (current, total, message)
            uow: UnitOfWork instance for database operations (required)

        Returns:
            OperationResult with import statistics and processed plays

        Raises:
            ValueError: If file_path is missing or invalid
            FileNotFoundError: If file doesn't exist
        """
        return await self.import_data(
            file_path=file_path,
            import_batch_id=import_batch_id,
            progress_callback=progress_callback,
            uow=uow,
        )

    async def _fetch_data(
        self, progress_callback: Callable[[int, int, str], None] | None = None, **kwargs
    ) -> list[Any]:
        """Fetch and parse Spotify JSON export file.

        Args:
            progress_callback: Optional function for progress updates
            **kwargs: Must contain 'file_path' key with Path to JSON file

        Returns:
            List of raw Spotify play records from the file

        Raises:
            ValueError: If file_path is missing or file is invalid
            FileNotFoundError: If file doesn't exist
        """
        file_path = kwargs.get("file_path")
        if not file_path:
            raise ValueError("file_path is required for Spotify file imports")

        # File validation
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        if not file_path.is_file():
            raise ValueError(f"Path is not a file: {file_path}")

        if progress_callback:
            progress_callback(10, 100, f"Parsing file: {file_path.name}")

        # Parse all records from file
        raw_records = await self.spotify_adapter.parse_file(file_path)

        if progress_callback:
            progress_callback(30, 100, f"Parsed {len(raw_records)} records from file")

        logger.info(f"Parsed {len(raw_records)} records from Spotify file: {file_path}")
        return raw_records

    async def _process_data(
        self,
        raw_data: list[Any],
        batch_id: str,
        import_timestamp: datetime,
        progress_callback: Callable[[int, int, str], None] | None = None,
        uow: Any | None = None,
        **_kwargs,
    ) -> list[TrackPlay]:
        """Convert raw Spotify records into TrackPlay objects.

        Processes records in configurable batches for optimal memory usage and
        transaction management.

        Args:
            raw_data: Raw Spotify play records from file
            batch_id: Unique identifier for this import batch
            import_timestamp: When this import was initiated
            progress_callback: Optional function for progress updates
            uow: UnitOfWork instance for database operations
            **kwargs: Additional parameters (unused for file imports)

        Returns:
            List of TrackPlay objects ready for database insertion
        """
        if not raw_data:
            return []

        # Get batch size from configuration
        batch_size_config = get_config("IMPORT_BATCH_SIZE", 1000)
        batch_size = int(batch_size_config) if batch_size_config is not None else 1000

        all_track_plays = []
        total_batches = (len(raw_data) + batch_size - 1) // batch_size

        # Aggregate filtering statistics across all batches
        total_filtering_stats = {
            "raw_plays": len(raw_data),
            "accepted_plays": 0,
            "duration_excluded": 0,
            "incognito_excluded": 0,
            "error_count": 0,
            "new_tracks_count": 0,
            "updated_tracks_count": 0,
        }

        if progress_callback:
            progress_callback(
                40,
                100,
                f"Processing {len(raw_data)} records in {total_batches} batches",
            )

        logger.info(f"Processing {len(raw_data)} records in batches of {batch_size}")

        # Process records in batches for memory efficiency
        for i in range(0, len(raw_data), batch_size):
            batch_records = raw_data[i : i + batch_size]
            batch_num = (i // batch_size) + 1

            if progress_callback:
                progress_percent = 40 + int(
                    (batch_num / total_batches) * 40
                )  # 40-80% range
                progress_callback(
                    progress_percent,
                    100,
                    f"Processing batch {batch_num}/{total_batches} ({len(batch_records)} records)",
                )

            logger.info(
                f"Processing batch {batch_num}/{total_batches} ({len(batch_records)} records)"
            )

            try:
                # Process batch through adapter - this handles track resolution
                (
                    batch_track_plays,
                    batch_filtering_stats,
                ) = await self.spotify_adapter.process_records(
                    records=batch_records,
                    batch_id=batch_id,
                    import_timestamp=import_timestamp,
                    uow=uow,
                )

                all_track_plays.extend(batch_track_plays)

                # Aggregate filtering statistics
                total_filtering_stats["accepted_plays"] += batch_filtering_stats[
                    "accepted_plays"
                ]
                total_filtering_stats["duration_excluded"] += batch_filtering_stats[
                    "duration_excluded"
                ]
                total_filtering_stats["incognito_excluded"] += batch_filtering_stats[
                    "incognito_excluded"
                ]
                total_filtering_stats["error_count"] += batch_filtering_stats.get(
                    "error_count", 0
                )

                # Aggregate canonical track metrics (sum across batches)
                # Each batch processes different Spotify IDs, so summing gives total unique tracks
                total_filtering_stats["new_tracks_count"] += batch_filtering_stats.get(
                    "new_tracks_count", 0
                )
                total_filtering_stats["updated_tracks_count"] += (
                    batch_filtering_stats.get("updated_tracks_count", 0)
                )

                logger.info(
                    f"Batch {batch_num} processed: {len(batch_track_plays)} track plays created"
                )

            except Exception as e:
                logger.error(f"Batch {batch_num} processing failed: {e}")
                # Continue with next batch - partial failures are acceptable
                continue

        if progress_callback:
            progress_callback(80, 100, f"Processed {len(all_track_plays)} track plays")

        logger.info(
            f"Spotify processing completed: {len(all_track_plays)} track plays created from {len(raw_data)} records"
        )

        # Store filtering stats for use in result creation
        self._last_filtering_stats = total_filtering_stats
        return all_track_plays

    def _create_success_result(
        self,
        raw_data: list[Any],
        track_plays: list[TrackPlay],
        imported_count: int,
        duplicate_count: int,
        batch_id: str,
    ) -> OperationResult:
        """Create success result with Spotify-specific filtering statistics."""
        from src.application.utilities.results import ImportResultData, ResultFactory

        # Calculate filtering count from stored stats
        filtering_stats = getattr(self, "_last_filtering_stats", {})
        filtered_count = filtering_stats.get(
            "duration_excluded", 0
        ) + filtering_stats.get("incognito_excluded", 0)
        error_count = filtering_stats.get("error_count", 0)

        import_data = ImportResultData(
            raw_data_count=len(raw_data),
            imported_count=imported_count,
            filtered_count=filtered_count,
            duplicate_count=duplicate_count,
            error_count=error_count,
            new_tracks_count=filtering_stats.get("new_tracks_count", 0),
            updated_tracks_count=filtering_stats.get("updated_tracks_count", 0),
            batch_id=batch_id,
            tracks=track_plays,
        )

        return ResultFactory.create_import_result(
            operation_name=self.operation_name,
            import_data=import_data,
        )

    async def _handle_checkpoints(self, raw_data: list[Any], **_kwargs) -> None:  # noqa: ARG002
        """Handle sync checkpoints for file-based imports.

        File-based imports don't need checkpoints since they process complete files.
        This is a no-op implementation to satisfy the BasePlayImporter interface.

        Args:
            raw_data: Raw data that was processed (unused for file imports)
            **_kwargs: Additional parameters (unused for file imports)
        """
        # File-based imports don't need checkpoints - files are processed completely
        # This is intentionally a no-op to satisfy the abstract method requirement
        logger.debug("Checkpoint handling skipped for file-based Spotify import")
