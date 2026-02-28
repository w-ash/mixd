"""Base class for importing music listening data from external sources."""

from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import Any, TypedDict
from uuid import uuid4

from src.config import get_logger
from src.domain.entities import ConnectorTrackPlay, OperationResult
from src.domain.entities.progress import (
    NullProgressEmitter,
    OperationStatus,
    ProgressEmitter,
    ProgressOperation,
    ProgressStatus,
    create_progress_event,
)
from src.domain.repositories.interfaces import (
    PlaysRepositoryProtocol,
    UnitOfWorkProtocol,
)
from src.domain.results import (
    ImportResultData,
    create_error_result,
    create_import_result,
)

logger = get_logger(__name__)


class CommonImportParams(TypedDict, total=False):
    """Common import parameters shared across all importers."""

    import_batch_id: str | None
    progress_emitter: ProgressEmitter
    uow: UnitOfWorkProtocol | None


class LastFMImportParams(CommonImportParams, total=False):
    """Last.fm-specific import parameters."""

    username: str | None
    from_date: datetime | None
    to_date: datetime | None
    limit: int | None


class SpotifyImportParams(CommonImportParams, total=False):
    """Spotify-specific import parameters."""

    file_path: str | None
    batch_size: int | None


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

    plays_repository: PlaysRepositoryProtocol | None
    operation_name: str

    def __init__(self, plays_repository: PlaysRepositoryProtocol | None) -> None:
        """Initialize with database repository for saving track plays.

        Args:
            plays_repository: Repository for persisting TrackPlay objects.
                Can be None for services using connector-based deferred resolution.
        """
        self.plays_repository = plays_repository
        self.operation_name = "Base Import"  # Override in subclasses
        self._saved_connector_plays: list[
            ConnectorTrackPlay
        ] = []  # For orchestrator retrieval

    async def import_data(
        self,
        import_batch_id: str | None = None,
        progress_emitter: ProgressEmitter | None = None,
        uow: UnitOfWorkProtocol | None = None,
        **kwargs: Any,
    ) -> OperationResult:
        """Import music listening data from external source to database.

        Orchestrates the complete import process: fetches data from source, converts
        to TrackPlay objects, saves to database with deduplication, and updates sync
        checkpoints. Provides progress tracking and handles errors gracefully.

        Args:
            import_batch_id: Optional batch ID for grouping related imports. Generated
                if not provided.
            progress_emitter: Optional progress emitter (defaults to null implementation)
            uow: UnitOfWork instance for database operations (required).
            **kwargs: Source-specific parameters passed to fetch/process methods.

        Returns:
            OperationResult containing import statistics and any TrackPlay objects
            that were processed.
        """
        if progress_emitter is None:
            progress_emitter = NullProgressEmitter()

        # Step 1: Setup import context
        batch_id = import_batch_id or str(uuid4())
        import_timestamp = datetime.now(UTC)

        # Start progress tracking
        operation = ProgressOperation(
            description=f"{self.operation_name} - Import play data using {self.__class__.__name__}",
            total_items=None,  # Unknown until we fetch data
        )
        operation_id = await progress_emitter.start_operation(operation)

        logger.info(
            f"Starting {self.operation_name}",
            batch_id=batch_id,
            service=self.__class__.__name__,
        )

        try:
            # Step 2: Fetch raw data (Strategy pattern - implemented by subclasses)
            await progress_emitter.emit_progress(
                create_progress_event(
                    operation_id=operation_id,
                    current=20,
                    total=100,
                    message="Fetching data...",
                    status=ProgressStatus.IN_PROGRESS,
                )
            )

            raw_data = await self._fetch_data(
                progress_emitter=progress_emitter, uow=uow, **kwargs
            )

            if not raw_data:
                # Handle empty data case - still call checkpoints for consistency
                await progress_emitter.emit_progress(
                    create_progress_event(
                        operation_id=operation_id,
                        current=90,
                        total=100,
                        message="No data to import - updating checkpoints...",
                        status=ProgressStatus.IN_PROGRESS,
                    )
                )

                await self._handle_checkpoints(raw_data=raw_data, uow=uow, **kwargs)

                await progress_emitter.complete_operation(
                    operation_id, OperationStatus.COMPLETED
                )
                return self._create_empty_result(batch_id)

            # Step 3: Process raw data into TrackPlay objects (Strategy pattern)
            await progress_emitter.emit_progress(
                create_progress_event(
                    operation_id=operation_id,
                    current=60,
                    total=100,
                    message=f"Processing {len(raw_data)} records...",
                    status=ProgressStatus.IN_PROGRESS,
                )
            )

            track_plays = await self._process_data(
                raw_data=raw_data,
                batch_id=batch_id,
                import_timestamp=import_timestamp,
                progress_emitter=progress_emitter,
                uow=uow,
                **kwargs,
            )

            # Step 4: Save to database (Template - always the same)
            await progress_emitter.emit_progress(
                create_progress_event(
                    operation_id=operation_id,
                    current=80,
                    total=100,
                    message=f"Saving {len(track_plays)} plays to database...",
                    status=ProgressStatus.IN_PROGRESS,
                )
            )

            imported_count, duplicate_count = await self._save_data(track_plays, uow)

            # Step 5: Handle checkpoints (Strategy pattern - delegated to subclasses)
            await progress_emitter.emit_progress(
                create_progress_event(
                    operation_id=operation_id,
                    current=90,
                    total=100,
                    message="Updating checkpoints...",
                    status=ProgressStatus.IN_PROGRESS,
                )
            )

            try:
                await self._handle_checkpoints(raw_data=raw_data, uow=uow, **kwargs)
            except Exception as e:
                logger.error(
                    f"Checkpoint handling failed: {e}",
                    batch_id=batch_id,
                    service=self.__class__.__name__,
                    error_type=type(e).__name__,
                    error_str=str(e),
                )
                raise

            # Step 6: Create success result (Template - standardized format)
            await progress_emitter.complete_operation(
                operation_id, OperationStatus.COMPLETED
            )

            logger.info(
                f"{self.operation_name} completed successfully",
                batch_id=batch_id,
                processed=len(raw_data),
                imported=imported_count,
            )

            result = self._create_success_result(
                raw_data=raw_data,
                processed_data=track_plays,
                imported_count=imported_count,
                duplicate_count=duplicate_count,
                batch_id=batch_id,
            )

        except Exception as e:
            # Standardized error handling with full exception details
            error_msg = f"{self.operation_name} failed: {e}"
            logger.opt(exception=True).error(
                error_msg,
                batch_id=batch_id,
                error=str(e),
                error_type=type(e).__name__,
            )

            await progress_emitter.complete_operation(
                operation_id, OperationStatus.FAILED
            )
            return self._create_error_result(error_msg, batch_id)
        else:
            return result

    @abstractmethod
    async def _fetch_data(
        self,
        progress_emitter: ProgressEmitter | None = None,
        uow: UnitOfWorkProtocol | None = None,
        **kwargs: Any,
    ) -> list[Any]:
        """Fetch raw listening data from external source.

        Implemented by each subclass to retrieve data from their specific source
        (e.g., Spotify API, Last.fm API, CSV files). Should return raw data objects
        that will be processed into TrackPlay objects.

        Args:
            progress_emitter: Progress emitter for operation status tracking.
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
        progress_emitter: ProgressEmitter | None = None,
        uow: UnitOfWorkProtocol | None = None,
        **kwargs: Any,
    ) -> list[Any]:
        """Convert raw source data into standardized domain objects.

        Implemented by each subclass to parse their specific data format and create
        domain objects (TrackPlay for immediate resolution, ConnectorTrackPlay for deferred).
        Should handle data cleaning and validation.

        Args:
            raw_data: Raw data objects returned from _fetch_data.
            batch_id: Unique identifier for this import batch.
            import_timestamp: When this import was initiated.
            progress_emitter: Progress emitter for operation status tracking.
            **kwargs: Source-specific processing parameters.

        Returns:
            Domain objects ready for database insertion (TrackPlay or ConnectorTrackPlay).
        """

    @abstractmethod
    async def _handle_checkpoints(
        self, raw_data: list[Any], uow: Any | None = None, **kwargs: Any
    ) -> None:
        """Update sync checkpoints to track import progress for incremental syncs.

        Implemented by each subclass to store markers (timestamps, cursor values, etc.)
        that indicate how much data has been imported. Used to resume imports from the
        last successful point rather than re-importing all historical data.

        Args:
            raw_data: Data that was successfully processed in this import.
            **kwargs: Source-specific checkpoint parameters and strategies.
        """

    def _store_connector_plays(self, connector_plays: list[ConnectorTrackPlay]) -> None:
        """Store connector plays for orchestrator retrieval.

        Args:
            connector_plays: List of connector plays to store for later orchestrator access.
        """
        self._saved_connector_plays = connector_plays

    def _get_stored_connector_plays(self) -> list[ConnectorTrackPlay]:
        """Retrieve stored connector plays for orchestrator.

        Returns:
            List of connector plays stored from the most recent import.
        """
        return self._saved_connector_plays

    async def _save_connector_plays_via_uow(
        self, connector_plays: list[ConnectorTrackPlay], uow: UnitOfWorkProtocol
    ) -> tuple[int, int]:
        """Common logic for saving connector plays via UnitOfWork.

        Args:
            connector_plays: ConnectorTrackPlay objects to persist.
            uow: UnitOfWork instance for repository access.

        Returns:
            Tuple of (inserted_count, duplicate_count) - duplicate_count is always 0 for connector plays.
        """
        if not connector_plays:
            self._store_connector_plays([])
            return 0, 0

        connector_play_repository = uow.get_connector_play_repository()
        _ = await connector_play_repository.bulk_insert_connector_plays(connector_plays)

        logger.info(f"💾 Saved {len(connector_plays)} connector plays via UnitOfWork")
        self._store_connector_plays(connector_plays)
        return len(connector_plays), 0  # No duplicates for connector plays

    async def _save_data(
        self, data: list[Any], uow: UnitOfWorkProtocol | None = None
    ) -> tuple[int, int]:
        """Save processed data to database with automatic deduplication.

        Args:
            data: Processed objects to persist (TrackPlay or ConnectorTrackPlay).
            uow: Unit of work for database operations (required).

        Returns:
            tuple[int, int]: (inserted_count, duplicate_count)
        """
        if not data:
            return (0, 0)

        # Modern UnitOfWork pattern - no legacy fallbacks
        if uow is None:
            raise ValueError("UnitOfWork is required for _save_data")

        plays_repository = uow.get_plays_repository()

        try:
            (
                inserted_count,
                duplicate_count,
            ) = await plays_repository.bulk_insert_plays(data)

            # Log database operation results with visibility
            if inserted_count > 0:
                logger.info(f"💾 Saved {inserted_count} new plays to database")
            if duplicate_count > 0:
                logger.info(f"🔄 Filtered {duplicate_count} duplicate plays")

        except Exception as e:
            logger.error(
                f"bulk_insert_plays failed with exception: {e}",
                sent_count=len(data),
                error_type=type(e).__name__,
                error_str=str(e),
            )
            raise
        else:
            return (inserted_count, duplicate_count)

    def _create_success_result(
        self,
        raw_data: list[Any],
        processed_data: list[Any],
        imported_count: int,
        duplicate_count: int,
        batch_id: str,
    ) -> OperationResult:
        """Create success result with import statistics.

        Template method that builds base import data and allows subclasses to enrich
        with service-specific statistics via _enrich_import_data().

        Args:
            raw_data: Raw data that was processed.
            processed_data: Processed objects that were created (TrackPlay or ConnectorTrackPlay).
            imported_count: Number of new items saved to database.
            duplicate_count: Number of duplicate items found.
            batch_id: Unique identifier for this import batch.

        Returns:
            OperationResult indicating successful import with statistics.
        """
        # Create base import data structure
        import_data = ImportResultData(
            raw_data_count=len(raw_data),
            imported_count=imported_count,
            duplicate_count=duplicate_count,
            batch_id=batch_id,
            tracks=processed_data,  # Note: this might contain ConnectorTrackPlay objects
        )

        # Allow subclasses to enrich with service-specific statistics
        enriched_data = self._enrich_import_data(import_data, raw_data, processed_data)

        return create_import_result(
            operation_name=self.operation_name,
            import_data=enriched_data,
        )

    def _enrich_import_data(
        self,
        base_data: ImportResultData,
        raw_data: list[Any],
        processed_data: list[Any],
    ) -> ImportResultData:
        """Enrich import data with service-specific statistics.

        Template method for subclasses to add service-specific metrics like
        filtering counts, track resolution stats, etc.

        Args:
            base_data: Base import data structure
            raw_data: Original raw data that was processed
            processed_data: Final processed objects that were created (TrackPlay or ConnectorTrackPlay)

        Returns:
            Enriched ImportResultData with service-specific statistics
        """
        # Base implementation returns data unchanged - subclasses can override
        _ = raw_data, processed_data  # Mark as unused in base implementation
        return base_data

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
        return create_import_result(
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
        return create_error_result(
            operation_name=self.operation_name,
            error_message=error_msg,
            batch_id=batch_id,
        )

    @staticmethod
    def _extract_common_params(
        **params: Any,
    ) -> tuple[CommonImportParams, dict[str, Any]]:
        """Extract common import parameters and return them with remaining service-specific params.

        Args:
            **params: All import parameters

        Returns:
            Tuple of (common_params, remaining_service_specific_params)
        """
        common_params: CommonImportParams = {
            "import_batch_id": params.get("import_batch_id"),
            "progress_emitter": params.get("progress_emitter", NullProgressEmitter()),
            "uow": params.get("uow"),
        }

        # Remove common parameters from the original params dict
        remaining_params = {
            k: v
            for k, v in params.items()
            if k not in {"import_batch_id", "progress_emitter", "uow"}
        }

        return common_params, remaining_params
