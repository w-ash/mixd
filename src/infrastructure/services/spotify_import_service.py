"""Spotify play history import service following clean architecture patterns.

Imports play data from Spotify data export JSON files using the BasePlayImporter
template method pattern for consistency with other music service imports.
"""

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.application.utilities.results import ImportResultData
from src.application.utilities.import_batch_processor import ImportBatchProcessor
from src.config import get_logger, settings
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
        
        # Create import batch processor optimized for file processing
        self.batch_processor = ImportBatchProcessor[list, tuple[list, dict]](
            batch_size=settings.import_settings.batch_size,
            retry_count=3,  # Simple retry for transient processing errors
            retry_base_delay=1.0,  # No need for API-style exponential backoff
            memory_limit_mb=100,  # Conservative memory limit for import operations
            logger_instance=logger,
        )

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
        self,
        progress_callback: Callable[[int, int, str], None] | None = None,
        uow: Any | None = None,
        **kwargs,
    ) -> list[Any]:
        """Fetch and parse Spotify JSON export file.

        Args:
            progress_callback: Optional function for progress updates
            uow: UnitOfWork (unused for file-based imports)
            **kwargs: Must contain 'file_path' key with Path to JSON file

        Returns:
            List of raw Spotify play records from the file

        Raises:
            ValueError: If file_path is missing or file is invalid
            FileNotFoundError: If file doesn't exist
        """
        # uow is not used for file-based imports (no database operations during fetch)
        _ = uow

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

        all_track_plays = []

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
                f"Processing {len(raw_data)} records using BatchProcessor",
            )

        async def process_batch(batch_records: list) -> tuple[list, dict]:
            """Process a single batch of records through the adapter."""
            return await self.spotify_adapter.process_records(
                records=batch_records,
                batch_id=batch_id,
                import_timestamp=import_timestamp,
                uow=uow,
            )

        # Custom progress callback that integrates with the legacy progress system
        def batch_progress_callback(event_type: str, data: dict):
            if event_type == "track_processed" and progress_callback:
                # Map BatchProcessor progress to legacy progress range (40-80%)
                items_processed = data.get("items_processed", 0)
                total_items = data.get("total_items", len(raw_data))
                if total_items > 0:
                    progress_percent = 40 + int((items_processed / total_items) * 40)
                    progress_callback(
                        progress_percent,
                        100,
                        f"Processed {items_processed}/{total_items} records",
                    )

        # Split data into batches and process using unified BatchProcessor
        batch_size = self.batch_processor.batch_size
        batches = [raw_data[i:i + batch_size] for i in range(0, len(raw_data), batch_size)]
        
        batch_results = await self.batch_processor.process(
            items=batches,
            process_func=process_batch,
            progress_callback=batch_progress_callback,
            progress_description=f"Processing {len(raw_data)} Spotify records"
        )

        # Aggregate results from all batches
        for batch_track_plays, batch_filtering_stats in batch_results:
            all_track_plays.extend(batch_track_plays)

            # Aggregate filtering statistics
            total_filtering_stats["accepted_plays"] += batch_filtering_stats["accepted_plays"]
            total_filtering_stats["duration_excluded"] += batch_filtering_stats["duration_excluded"]
            total_filtering_stats["incognito_excluded"] += batch_filtering_stats["incognito_excluded"]
            total_filtering_stats["error_count"] += batch_filtering_stats.get("error_count", 0)
            total_filtering_stats["new_tracks_count"] += batch_filtering_stats.get("new_tracks_count", 0)
            total_filtering_stats["updated_tracks_count"] += batch_filtering_stats.get("updated_tracks_count", 0)

        if progress_callback:
            progress_callback(80, 100, f"Processed {len(all_track_plays)} track plays")

        logger.info(
            f"Spotify processing completed: {len(all_track_plays)} track plays created from {len(raw_data)} records"
        )

        # Store filtering stats for use in result creation
        self._last_filtering_stats = total_filtering_stats
        return all_track_plays

    def _enrich_import_data(
        self,
        base_data: "ImportResultData",
        raw_data: list[Any],  # noqa: ARG002 - Used for statistics calculation
        track_plays: list[TrackPlay],  # noqa: ARG002 - Used for statistics calculation
    ) -> "ImportResultData":
        """Enrich import data with Spotify-specific filtering statistics."""
        from src.application.utilities.results import ImportResultData

        # Calculate filtering count from stored stats
        filtering_stats = getattr(self, "_last_filtering_stats", {})
        filtered_count = filtering_stats.get(
            "duration_excluded", 0
        ) + filtering_stats.get("incognito_excluded", 0)
        error_count = filtering_stats.get("error_count", 0)

        # Create enriched data with Spotify-specific statistics
        return ImportResultData(
            raw_data_count=base_data.raw_data_count,
            imported_count=base_data.imported_count,
            filtered_count=filtered_count,
            duplicate_count=base_data.duplicate_count,
            error_count=error_count,
            new_tracks_count=filtering_stats.get("new_tracks_count", 0),
            updated_tracks_count=filtering_stats.get("updated_tracks_count", 0),
            batch_id=base_data.batch_id,
            tracks=base_data.tracks,
        )

    async def _handle_checkpoints(
        self, raw_data: list[Any], uow: Any | None = None, **_kwargs  # noqa: ARG002
    ) -> None:
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
