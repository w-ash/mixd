"""Base class for importing music listening data from external sources."""

from abc import ABC, abstractmethod
from datetime import UTC, datetime
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
from src.domain.repositories.play import PlayImportParams
from src.domain.repositories.uow import UnitOfWorkProtocol
from src.domain.results import (
    ImportResultData,
    create_error_result,
    create_import_result,
)

logger = get_logger(__name__)


class BasePlayImporter[TRawData, TParams: PlayImportParams](ABC):
    """Base class for importing music listening data from external sources.

    Provides the common workflow for importing track plays from sources like
    Spotify exports or the Last.fm API: progress tracking, error handling, and
    connector-play persistence, while subclasses implement source-specific
    fetching, parsing, and checkpointing against their own frozen ``TParams``
    object.

    Each import creates a batch with unique ID for tracking and rollback
    purposes. Plays are persisted as connector plays (deferred resolution).

    Workflow:
        1. Generate batch ID and timestamp
        2. Fetch raw data from source (implemented by subclass)
        3. Convert to ConnectorTrackPlay objects (implemented by subclass)
        4. Save connector plays via the UnitOfWork
        5. Update sync checkpoints (implemented by subclass)
        6. Return import statistics + the saved connector plays
    """

    operation_name: str = "Base Import"  # Override in subclasses

    async def import_data(
        self,
        params: TParams,
        *,
        uow: UnitOfWorkProtocol,
        progress_emitter: ProgressEmitter | None = None,
        import_batch_id: str | None = None,
    ) -> tuple[OperationResult, list[ConnectorTrackPlay]]:
        """Import music listening data from an external source to the database.

        Orchestrates the complete import: fetch, convert, persist as connector
        plays, checkpoint, and report. Errors are converted to an error result
        (never raised) so a failed import surfaces as statistics, not a stack
        trace.

        Args:
            params: Source-specific frozen import selectors.
            uow: UnitOfWork for database operations.
            progress_emitter: Optional progress emitter (defaults to null).
            import_batch_id: Optional batch ID for grouping related imports;
                generated when omitted.

        Returns:
            Tuple of (operation result, connector plays saved this run).
        """
        if progress_emitter is None:
            progress_emitter = NullProgressEmitter()

        batch_id = import_batch_id or str(uuid4())
        import_timestamp = datetime.now(UTC)

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
            result = await self._run_import_pipeline(
                params,
                progress_emitter=progress_emitter,
                operation_id=operation_id,
                batch_id=batch_id,
                import_timestamp=import_timestamp,
                uow=uow,
            )

        except Exception as e:
            error_msg = f"{self.operation_name} failed: {e}"
            logger.error(
                error_msg,
                batch_id=batch_id,
                error=str(e),
                error_type=type(e).__name__,
                exc_info=True,
            )

            await progress_emitter.complete_operation(
                operation_id, OperationStatus.FAILED
            )
            return self._create_error_result(error_msg, batch_id), []
        else:
            return result

    async def _run_import_pipeline(
        self,
        params: TParams,
        *,
        progress_emitter: ProgressEmitter,
        operation_id: str,
        batch_id: str,
        import_timestamp: datetime,
        uow: UnitOfWorkProtocol,
    ) -> tuple[OperationResult, list[ConnectorTrackPlay]]:
        """Fetch → process → save → checkpoint pipeline body for :meth:`import_data`."""
        # Step 2: Fetch raw data (implemented by subclasses)
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
            params,
            uow=uow,
            progress_emitter=progress_emitter,
            operation_id=operation_id,
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

            await self._handle_checkpoints(raw_data, params, uow)

            await progress_emitter.complete_operation(
                operation_id, OperationStatus.COMPLETED
            )
            return self._create_empty_result(batch_id), []

        # Step 3: Process raw data into ConnectorTrackPlay objects
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
            raw_data,
            batch_id=batch_id,
            import_timestamp=import_timestamp,
        )

        # Step 4: Save connector plays (always the same path)
        await progress_emitter.emit_progress(
            create_progress_event(
                operation_id=operation_id,
                current=80,
                total=100,
                message=f"Saving {len(track_plays)} plays to database...",
                status=ProgressStatus.IN_PROGRESS,
            )
        )

        imported_count, duplicate_count = await self._save_connector_plays_via_uow(
            track_plays, uow
        )

        # Step 5: Handle checkpoints (delegated to subclasses)
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
            await self._handle_checkpoints(raw_data, params, uow)
        except Exception as e:
            logger.error(
                f"Checkpoint handling failed: {e}",
                batch_id=batch_id,
                service=self.__class__.__name__,
                error_type=type(e).__name__,
                error_str=str(e),
            )
            raise

        # Step 6: Create success result (standardized format)
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
        return result, track_plays

    @abstractmethod
    async def _fetch_data(
        self,
        params: TParams,
        *,
        uow: UnitOfWorkProtocol,
        progress_emitter: ProgressEmitter | None = None,
        operation_id: str | None = None,
    ) -> list[TRawData]:
        """Fetch raw listening data from the external source.

        Implemented by each subclass to retrieve data from their specific source
        (e.g., Last.fm API, Spotify export files).

        Args:
            params: Source-specific import selectors.
            uow: UnitOfWork for checkpoint reads/writes during fetching.
            progress_emitter: Progress emitter for operation status tracking.
            operation_id: Operation ID for progress event emission.

        Returns:
            Raw data objects from the source, ready for processing.
        """

    @abstractmethod
    async def _process_data(
        self,
        raw_data: list[TRawData],
        *,
        batch_id: str,
        import_timestamp: datetime,
    ) -> list[ConnectorTrackPlay]:
        """Convert raw source data into ConnectorTrackPlay objects.

        Implemented by each subclass to parse their specific data format.

        Args:
            raw_data: Raw data objects returned from _fetch_data.
            batch_id: Unique identifier for this import batch.
            import_timestamp: When this import was initiated.

        Returns:
            Connector plays ready for database insertion.
        """

    @abstractmethod
    async def _handle_checkpoints(
        self,
        raw_data: list[TRawData],
        params: TParams,
        uow: UnitOfWorkProtocol,
    ) -> None:
        """Update sync checkpoints to track import progress for incremental syncs.

        Implemented by each subclass to store markers (timestamps, cursor values)
        indicating how much data has been imported. May be a no-op for sources
        that checkpoint during fetching (Last.fm) or not at all (file imports).

        Args:
            raw_data: Data that was successfully processed in this import.
            params: Source-specific import selectors.
            uow: UnitOfWork for checkpoint persistence.
        """

    async def _save_connector_plays_via_uow(
        self, connector_plays: list[ConnectorTrackPlay], uow: UnitOfWorkProtocol
    ) -> tuple[int, int]:
        """Persist connector plays — the single save path for all importers.

        Args:
            connector_plays: ConnectorTrackPlay objects to persist.
            uow: UnitOfWork instance for repository access.

        Returns:
            Tuple of (inserted_count, duplicate_count) — duplicate_count is
            always 0 for connector plays.
        """
        if not connector_plays:
            return 0, 0

        connector_play_repository = uow.get_connector_play_repository()
        _ = await connector_play_repository.bulk_insert_connector_plays(connector_plays)

        logger.info(f"💾 Saved {len(connector_plays)} connector plays via UnitOfWork")
        return len(connector_plays), 0  # No duplicates for connector plays

    def _create_success_result(
        self,
        raw_data: list[TRawData],
        processed_data: list[ConnectorTrackPlay],
        imported_count: int,
        duplicate_count: int,
        batch_id: str,
    ) -> OperationResult:
        """Create success result with import statistics.

        Builds the base import data and lets subclasses enrich it with
        service-specific statistics via :meth:`_enrich_import_data`.
        """
        import_data = ImportResultData(
            raw_data_count=len(raw_data),
            imported_count=imported_count,
            duplicate_count=duplicate_count,
            batch_id=batch_id,
            tracks=processed_data,  # Note: contains ConnectorTrackPlay objects
        )

        enriched_data = self._enrich_import_data(import_data, raw_data, processed_data)

        return create_import_result(
            operation_name=self.operation_name,
            import_data=enriched_data,
        )

    def _enrich_import_data(
        self,
        base_data: ImportResultData,
        raw_data: list[TRawData],
        processed_data: list[ConnectorTrackPlay],
    ) -> ImportResultData:
        """Enrich import data with service-specific statistics.

        Hook for subclasses to add metrics like filtering counts or track
        resolution stats. Base implementation returns the data unchanged.
        """
        _ = raw_data, processed_data  # Mark as unused in base implementation
        return base_data

    def _create_empty_result(self, batch_id: str) -> OperationResult:
        """Create result when no data was available to import."""
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
        """Create result when import failed due to an error."""
        return create_error_result(
            operation_name=self.operation_name,
            error_message=error_msg,
            batch_id=batch_id,
        )
