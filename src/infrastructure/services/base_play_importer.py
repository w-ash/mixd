"""Base class for importing music listening data from external sources."""

from abc import ABC, abstractmethod
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from src.application.utilities.results import ImportResultData, ResultFactory
from src.config import get_logger
from src.domain.entities import OperationResult, TrackPlay
from src.domain.repositories.interfaces import PlaysRepositoryProtocol

logger = get_logger(__name__)


class BasePlayImporter(ABC):
    """Base class for importing music listening data from external sources.

    Provides common workflow for importing track plays from sources like Spotify, Last.fm,
    or local files. Handles database persistence, progress tracking, and error handling
    while letting subclasses implement source-specific data fetching and parsing.

    Each import creates a batch with unique ID for tracking and rollback purposes.
    Automatically deduplicates plays already in the database.

    Workflow:
        1. Generate batch ID and timestamp
        2. Fetch raw data from source (implemented by subclass)
        3. Convert to TrackPlay objects (implemented by subclass)
        4. Save to database with deduplication
        5. Update sync checkpoints (implemented by subclass)
        6. Return import statistics
    """

    def __init__(self, plays_repository: PlaysRepositoryProtocol) -> None:
        """Initialize with database repository for saving track plays.

        Args:
            plays_repository: Repository for persisting TrackPlay objects.
        """
        self.plays_repository = plays_repository
        self.operation_name = "Base Import"  # Override in subclasses

    async def import_data(
        self,
        import_batch_id: str | None = None,
        progress_callback: Callable[[int, int, str], None] | None = None,
        **kwargs,
    ) -> OperationResult:
        """Import music listening data from external source to database.

        Orchestrates the complete import process: fetches data from source, converts
        to TrackPlay objects, saves to database with deduplication, and updates sync
        checkpoints. Provides progress tracking and handles errors gracefully.

        Args:
            import_batch_id: Optional batch ID for grouping related imports. Generated
                if not provided.
            progress_callback: Optional function called with (current, total, message)
                for UI progress updates.
            **kwargs: Source-specific parameters passed to fetch/process methods.

        Returns:
            OperationResult containing import statistics and any TrackPlay objects
            that were processed.
        """
        # Step 1: Setup import context
        batch_id = import_batch_id or str(uuid4())
        import_timestamp = datetime.now(UTC)

        if progress_callback:
            progress_callback(0, 100, "Starting import...")

        logger.info(
            f"Starting {self.operation_name}",
            batch_id=batch_id,
            service=self.__class__.__name__,
        )

        try:
            # Step 2: Fetch raw data (Strategy pattern - implemented by subclasses)
            if progress_callback:
                progress_callback(20, 100, "Fetching data...")

            raw_data = await self._fetch_data(
                progress_callback=progress_callback, **kwargs
            )

            if not raw_data:
                # Handle empty data case - still call checkpoints for consistency
                if progress_callback:
                    progress_callback(
                        90, 100, "No data to import - updating checkpoints..."
                    )

                await self._handle_checkpoints(raw_data=raw_data, **kwargs)

                if progress_callback:
                    progress_callback(100, 100, "No data to import")

                return self._create_empty_result(batch_id)

            # Step 3: Process raw data into TrackPlay objects (Strategy pattern)
            if progress_callback:
                progress_callback(60, 100, f"Processing {len(raw_data)} records...")

            # Import UnitOfWork here to avoid circular dependencies
            from src.infrastructure.persistence.database.db_connection import (
                get_session,
            )
            from src.infrastructure.persistence.repositories.factories import (
                get_unit_of_work,
            )

            # Create UnitOfWork context for track resolution and processing
            async with get_session() as session:
                uow = get_unit_of_work(session)
                track_plays = await self._process_data(
                    raw_data=raw_data,
                    batch_id=batch_id,
                    import_timestamp=import_timestamp,
                    progress_callback=progress_callback,
                    uow=uow,
                    **kwargs,
                )

            # Step 4: Save to database (Template - always the same)
            if progress_callback:
                progress_callback(
                    80, 100, f"Saving {len(track_plays)} plays to database..."
                )

            imported_count = await self._save_data(track_plays)

            # Step 5: Handle checkpoints (Strategy pattern - delegated to subclasses)
            if progress_callback:
                progress_callback(90, 100, "Updating checkpoints...")

            await self._handle_checkpoints(raw_data=raw_data, **kwargs)

            # Step 6: Create success result (Template - standardized format)
            if progress_callback:
                progress_callback(100, 100, "Import completed successfully")

            logger.info(
                f"{self.operation_name} completed successfully",
                batch_id=batch_id,
                processed=len(raw_data),
                imported=imported_count,
            )

            return self._create_success_result(
                raw_data=raw_data,
                track_plays=track_plays,
                imported_count=imported_count,
                batch_id=batch_id,
            )

        except Exception as e:
            # Standardized error handling
            error_msg = f"{self.operation_name} failed: {e}"
            logger.error(
                f"{self.operation_name} failed", batch_id=batch_id, error=str(e)
            )

            return self._create_error_result(error_msg, batch_id)

    @abstractmethod
    async def _fetch_data(
        self, progress_callback: Callable[[int, int, str], None] | None = None, **kwargs
    ) -> list[Any]:
        """Fetch raw listening data from external source.

        Implemented by each subclass to retrieve data from their specific source
        (e.g., Spotify API, Last.fm API, CSV files). Should return raw data objects
        that will be processed into TrackPlay objects.

        Args:
            progress_callback: Optional function for progress updates.
            **kwargs: Source-specific parameters (API keys, file paths, date ranges).

        Returns:
            Raw data objects from the source, ready for processing.
        """

    @abstractmethod
    async def _process_data(
        self,
        raw_data: list[Any],
        batch_id: str,
        import_timestamp: datetime,
        progress_callback: Callable[[int, int, str], None] | None = None,
        uow: Any | None = None,
        **kwargs,
    ) -> list[TrackPlay]:
        """Convert raw source data into standardized TrackPlay objects.

        Implemented by each subclass to parse their specific data format and create
        TrackPlay objects with normalized track/artist names, play timestamps, and
        metadata. Should handle data cleaning and validation.

        Args:
            raw_data: Raw data objects returned from _fetch_data.
            batch_id: Unique identifier for this import batch.
            import_timestamp: When this import was initiated.
            progress_callback: Optional function for progress updates.
            **kwargs: Source-specific processing parameters.

        Returns:
            TrackPlay objects ready for database insertion.
        """

    @abstractmethod
    async def _handle_checkpoints(self, raw_data: list[Any], **kwargs) -> None:
        """Update sync checkpoints to track import progress for incremental syncs.

        Implemented by each subclass to store markers (timestamps, cursor values, etc.)
        that indicate how much data has been imported. Used to resume imports from the
        last successful point rather than re-importing all historical data.

        Args:
            raw_data: Data that was successfully processed in this import.
            **kwargs: Source-specific checkpoint parameters and strategies.
        """

    async def _save_data(self, track_plays: list[TrackPlay]) -> int:
        """Save track plays to database with automatic deduplication.

        Args:
            track_plays: TrackPlay objects to persist.

        Returns:
            Number of new plays actually inserted (after deduplication).
        """
        if not track_plays:
            return 0

        return await self.plays_repository.bulk_insert_plays(track_plays)

    def _create_success_result(
        self,
        raw_data: list[Any],
        track_plays: list[TrackPlay],
        imported_count: int,
        batch_id: str,
    ) -> OperationResult:
        """Create success result with import statistics.

        Args:
            raw_data: Raw data that was processed.
            track_plays: TrackPlay objects that were created.
            imported_count: Number of new plays saved to database.
            batch_id: Unique identifier for this import batch.

        Returns:
            OperationResult indicating successful import with statistics.
        """
        import_data = ImportResultData(
            raw_data_count=len(raw_data),
            imported_count=imported_count,
            batch_id=batch_id,
            tracks=track_plays,
        )
        return ResultFactory.create_import_result(
            operation_name=self.operation_name,
            import_data=import_data,
        )

    def _create_empty_result(self, batch_id: str) -> OperationResult:
        """Create result when no data was available to import.

        Args:
            batch_id: Unique identifier for this import batch.

        Returns:
            OperationResult indicating no data was imported.
        """
        import_data = ImportResultData(
            raw_data_count=0,
            imported_count=0,
            batch_id=batch_id,
        )
        return ResultFactory.create_import_result(
            operation_name=self.operation_name,
            import_data=import_data,
        )

    def _create_error_result(self, error_msg: str, batch_id: str) -> OperationResult:
        """Create result when import failed due to an error.

        Args:
            error_msg: Description of what went wrong.
            batch_id: Unique identifier for this import batch.

        Returns:
            OperationResult indicating import failure with error details.
        """
        return ResultFactory.create_error_result(
            operation_name=self.operation_name,
            error_message=error_msg,
            batch_id=batch_id,
        )
