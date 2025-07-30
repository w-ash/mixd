"""Prevents memory overflows and API rate limiting when processing thousands of music items.

Splits large collections into manageable chunks for three operations:
- Importing: Parse music files/playlists into database records
- Matching: Find tracks on Spotify/Last.fm/MusicBrainz using search APIs
- Syncing: Transfer playlists/likes between music services

Tracks per-batch success rates and provides real-time progress updates.
"""

from abc import ABC, abstractmethod
from collections.abc import Callable, Coroutine, Sequence
from typing import Any, Protocol

from attrs import define, field

# Removed RepositoryProvider import - was unused after refactor


# Protocols for dependency injection
class ConfigProvider(Protocol):
    """Supplies batch sizes and API rate limits from app configuration."""

    def get(self, key: str, default: Any = None) -> Any:
        """Gets configuration value by key.

        Args:
            key: Configuration key to look up.
            default: Value to return if key is not found.

        Returns:
            The configuration value or default if not found.
        """
        ...


class Logger(Protocol):
    """Records batch processing progress and errors for debugging."""

    def info(self, message: str, **kwargs: Any) -> None:
        """Log informational message."""
        ...

    def debug(self, message: str, **kwargs: Any) -> None:
        """Log debug message."""
        ...

    def exception(self, message: str, **kwargs: Any) -> None:
        """Log error with exception details."""
        ...


@define(frozen=True)
class BatchResult:
    """Success rates and detailed outcomes from processing music items in batches.

    Aggregates results across all batches to show total items processed,
    success/error counts, and processing statistics for user feedback.

    Attributes:
        total_items: Total number of items submitted for processing.
        processed_count: Number of items that were processed (success or failure).
        batch_results: Detailed results from each batch, containing status and data.
    """

    total_items: int
    processed_count: int
    batch_results: list[list[dict]] = field(factory=list)

    @property
    def success_count(self) -> int:
        """Items successfully imported, processed, or synced."""
        return (
            self.get_status_count("imported")
            + self.get_status_count("processed")
            + self.get_status_count("synced")
        )

    @property
    def error_count(self) -> int:
        """Items that failed due to API errors or data issues."""
        return self.get_status_count("error")

    @property
    def skipped_count(self) -> int:
        """Items deliberately skipped (duplicates, invalid data, etc.)."""
        return self.get_status_count("skipped")

    @property
    def success_rate(self) -> float:
        """Success rate as percentage (0.0 to 100.0)."""
        if self.processed_count == 0:
            return 0.0
        return round((self.success_count / self.processed_count) * 100, 2)

    def get_status_count(self, status: str) -> int:
        """Counts items with specific status across all batches."""
        count = 0
        for batch in self.batch_results:
            for result in batch:
                if result.get("status") == status:
                    count += 1
        return count


class BatchStrategy[T](ABC):
    """Template for processing batches of music items with operation-specific logic.

    Subclasses define batch sizes and processing methods for imports,
    API matching, or service syncing operations.
    """

    def __init__(
        self, batch_size: int | None = None, config: ConfigProvider | None = None
    ):
        """Initializes with batch size and configuration access."""
        self.config = config
        self.batch_size = batch_size or self._get_default_batch_size()

    @abstractmethod
    def _get_default_batch_size(self) -> int:
        """Returns strategy-specific default batch size."""

    @abstractmethod
    async def process_batch(self, items: Sequence[T]) -> list[dict]:
        """Processes batch of items, returning status dictionaries for each."""


class ImportStrategy[T](BatchStrategy[T]):
    """Parses music files and playlists into database records.

    Processes each item individually through a custom function,
    catching import errors to continue processing the batch.
    """

    def __init__(
        self,
        processor_func: Callable[[T], Coroutine[Any, Any, dict]],
        batch_size: int | None = None,
        config: ConfigProvider | None = None,
        logger: Logger | None = None,
    ):
        """Initializes import strategy with processing function.

        Args:
            processor_func: Async function that processes a single import item.
            batch_size: Items per batch, uses config default if None.
            config: Configuration provider for settings.
            logger: Logger for capturing import progress and errors.
        """
        self.processor_func = processor_func
        self.logger = logger
        super().__init__(batch_size, config)

    def _get_default_batch_size(self) -> int:
        """Returns 50 items per batch for import operations."""
        if self.config:
            return self.config.get("DEFAULT_IMPORT_BATCH_SIZE", 50)
        return 50

    async def process_batch(self, items: Sequence[T]) -> list[dict]:
        """Processes each item individually, returning success/error status for each."""
        results = []
        for item in items:
            try:
                result = await self.processor_func(item)
                results.append(result)
            except Exception as e:
                if self.logger:
                    self.logger.exception(f"Error processing item in import batch: {e}")
                results.append({
                    "status": "error",
                    "error": str(e),
                })
        return results


class MatchStrategy[T](BatchStrategy[T]):
    """Finds tracks on Spotify, Last.fm, and MusicBrainz using search APIs.

    Respects API rate limits and applies confidence thresholds to ensure
    high-quality matches for music track identification.
    """

    def __init__(
        self,
        connector: Any,
        batch_size: int | None = None,
        confidence_threshold: float = 80.0,
        connector_type: str | None = None,
        processor_func: Callable[[list[T], Any], Coroutine[Any, Any, list[dict]]]
        | None = None,
        config: ConfigProvider | None = None,
        logger: Logger | None = None,
    ):
        """Initializes matching strategy with API connector.

        Args:
            connector: API connector for external music service.
            batch_size: Items per batch, uses connector-specific default if None.
            confidence_threshold: Minimum match confidence score (0-100).
            connector_type: Service type for config lookup (e.g., 'spotify', 'lastfm').
            processor_func: Custom function for batch matching logic.
            config: Configuration provider for API settings.
            logger: Logger for capturing match results and errors.
        """
        self.connector = connector
        self.confidence_threshold = confidence_threshold
        self.connector_type = connector_type
        self.processor_func = processor_func
        self.logger = logger
        super().__init__(batch_size, config)

    def _get_default_batch_size(self) -> int:
        """Returns 30 items per batch to respect API rate limits."""
        if self.config and self.connector_type:
            config_key = f"{self.connector_type.upper()}_API_BATCH_SIZE"
            return self.config.get(
                config_key, self.config.get("DEFAULT_MATCH_BATCH_SIZE", 30)
            )
        elif self.config:
            return self.config.get("DEFAULT_MATCH_BATCH_SIZE", 30)
        return 30

    async def process_batch(self, items: Sequence[T]) -> list[dict]:
        """Match tracks using the processor function and connector."""
        if self.processor_func:
            try:
                return await self.processor_func(list(items), self.connector)
            except Exception as e:
                if self.logger:
                    self.logger.exception(f"Error in custom match processor: {e}")
                return [{"status": "error", "error": str(e)} for _ in items]

        # No default matching implementation - must provide match_func
        raise NotImplementedError(
            "processor_func must be provided for matching operations"
        )


class SyncStrategy[T](BatchStrategy[T]):
    """Synchronizes music data between different services.

    Transfers playlists, likes, or other music data from one service to another
    (e.g., Spotify to Last.fm). Handles API rate limits and service-specific
    formatting requirements.
    """

    def __init__(
        self,
        source_service: str,
        target_service: str,
        batch_size: int | None = None,
        sync_func: Callable[[list[T]], Coroutine[Any, Any, list[dict]]] | None = None,
        connector: Any = None,
        config: ConfigProvider | None = None,
        logger: Logger | None = None,
    ):
        """Initialize sync strategy for transferring data between services.

        Args:
            source_service: Name of the service to sync from (e.g., 'spotify')
            target_service: Name of the service to sync to (e.g., 'lastfm')
            batch_size: Items per batch, uses target service limits if None
            sync_func: Custom function for syncing logic
            connector: API connector for the target service
            config: Configuration provider for service settings
            logger: Logger for capturing sync progress and errors
        """
        self.source_service = source_service
        self.target_service = target_service
        self.sync_func = sync_func
        self.connector = connector
        self.logger = logger
        super().__init__(batch_size, config)

    def _get_default_batch_size(self) -> int:
        """Default batch size based on target service API limits (20 items)."""
        if self.config:
            # Use target service config for API rate limiting
            config_key = f"{self.target_service.upper()}_API_BATCH_SIZE"
            return self.config.get(
                config_key, self.config.get("DEFAULT_SYNC_BATCH_SIZE", 20)
            )
        return 20

    async def process_batch(self, items: Sequence[T]) -> list[dict]:
        """Sync items using the sync function."""
        if self.sync_func:
            try:
                return await self.sync_func(list(items))
            except Exception as e:
                if self.logger:
                    self.logger.exception(f"Error in custom sync processor: {e}")
                return [{"status": "error", "error": str(e)} for _ in items]

        # No default sync implementation - must provide sync_func
        raise NotImplementedError("sync_func must be provided for sync operations")


class BatchProcessor[T]:
    """Processes large collections of music data in configurable chunks.

    Handles importing music files, matching tracks against APIs, and syncing data
    between music services. Prevents memory overflow and respects API rate limits
    by processing items in batches. Provides progress tracking and detailed
    success/failure metrics.
    """

    def __init__(
        self,
        logger: Logger | None = None,
    ):
        """Initialize with optional logger."""
        self.logger = logger

    async def process_with_strategy(
        self,
        items: Sequence[T],
        strategy: BatchStrategy[T],
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> BatchResult:
        """Process a collection of items using the specified processing strategy.

        Args:
            items: Collection of music data items to process
            strategy: Processing strategy (import, match, or sync)
            progress_callback: Optional function to report progress updates

        Returns:
            Detailed results including success/failure counts and processing metrics
        """
        if not items:
            if self.logger:
                self.logger.info("No items to process")
            return BatchResult(total_items=0, processed_count=0)

        total_items = len(items)
        processed_count = 0
        batch_results = []

        # Process items in batches according to strategy
        for i in range(0, total_items, strategy.batch_size):
            batch = items[i : i + strategy.batch_size]
            batch_start = i + 1
            batch_end = min(i + strategy.batch_size, total_items)

            if self.logger:
                self.logger.debug(
                    f"Processing batch {batch_start}-{batch_end} of {total_items}"
                )

            # Update progress if callback provided
            if progress_callback:
                progress_callback(
                    batch_end, total_items, f"Processing batch {len(batch_results) + 1}"
                )

            try:
                # Process the batch using the strategy
                batch_result = await strategy.process_batch(batch)
                batch_results.append(batch_result)
                processed_count += len(batch_result)

            except Exception as e:
                if self.logger:
                    self.logger.exception(
                        f"Error processing batch {batch_start}-{batch_end}: {e}"
                    )
                # Create error results for the entire batch
                error_results = [{"status": "error", "error": str(e)} for _ in batch]
                batch_results.append(error_results)
                processed_count += len(error_results)

        result = BatchResult(
            total_items=total_items,
            processed_count=processed_count,
            batch_results=batch_results,
        )

        if self.logger:
            self.logger.info(
                f"Batch processing completed: {result.success_count} successful, "
                f"{result.error_count} errors, {result.skipped_count} skipped "
                f"out of {total_items} total items"
            )

        return result

    def create_import_strategy(
        self,
        processor_func: Callable[[T], Coroutine[Any, Any, dict]],
        batch_size: int | None = None,
        config: ConfigProvider | None = None,
    ) -> ImportStrategy[T]:
        """Create import strategy for processing music data files."""
        return ImportStrategy(
            processor_func=processor_func,
            batch_size=batch_size,
            config=config,
            logger=self.logger,
        )

    def create_match_strategy(
        self,
        connector: Any,
        confidence_threshold: float = 80.0,
        connector_type: str | None = None,
        batch_size: int | None = None,
        processor_func: Callable[[list[T], Any], Coroutine[Any, Any, list[dict]]]
        | None = None,
        config: ConfigProvider | None = None,
    ) -> MatchStrategy[T]:
        """Create a strategy for matching tracks against external music APIs.

        Args:
            connector: API connector for the music service
            confidence_threshold: Minimum match confidence score (0-100)
            connector_type: Service type for config lookup (e.g., 'spotify')
            batch_size: Items per batch, uses connector default if None
            processor_func: Custom matching function
            config: Configuration provider for API settings

        Returns:
            Match strategy ready for use with process_with_strategy
        """
        return MatchStrategy(
            connector=connector,
            batch_size=batch_size,
            confidence_threshold=confidence_threshold,
            connector_type=connector_type,
            processor_func=processor_func,
            config=config,
            logger=self.logger,
        )

    def create_sync_strategy(
        self,
        source_service: str,
        target_service: str,
        batch_size: int | None = None,
        sync_func: Callable[[list[T]], Coroutine[Any, Any, list[dict]]] | None = None,
        connector: Any = None,
        config: ConfigProvider | None = None,
    ) -> SyncStrategy[T]:
        """Create sync strategy for transferring data between music services."""
        return SyncStrategy(
            source_service=source_service,
            target_service=target_service,
            batch_size=batch_size,
            sync_func=sync_func,
            connector=connector,
            config=config,
            logger=self.logger,
        )
