"""Unified batch processor to eliminate duplicate batch processing patterns.

This module provides a configurable batch processing system with different strategies,
replacing the multiple batch processing implementations scattered across services.

Clean Architecture compliant - no external dependencies, uses dependency injection.
"""

from abc import ABC, abstractmethod
from collections.abc import Callable, Coroutine, Sequence
from typing import Any, Protocol

from attrs import define, field

# Removed RepositoryProvider import - was unused after Clean Architecture refactor


# Protocols for dependency injection (Clean Architecture compliance)
class ConfigProvider(Protocol):
    """Protocol for configuration access."""

    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration value."""
        ...


class Logger(Protocol):
    """Protocol for logging."""

    def info(self, message: str, **kwargs: Any) -> None:
        """Log info message."""
        ...

    def debug(self, message: str, **kwargs: Any) -> None:
        """Log debug message."""
        ...

    def exception(self, message: str, **kwargs: Any) -> None:
        """Log exception message."""
        ...


@define(frozen=True)
class BatchResult:
    """Result of batch processing operation with aggregated metrics."""

    total_items: int
    processed_count: int
    batch_results: list[list[dict]] = field(factory=list)

    @property
    def success_count(self) -> int:
        """Count of successfully processed items."""
        return (
            self.get_status_count("imported")
            + self.get_status_count("processed")
            + self.get_status_count("synced")
        )

    @property
    def error_count(self) -> int:
        """Count of items that failed processing."""
        return self.get_status_count("error")

    @property
    def skipped_count(self) -> int:
        """Count of items that were skipped."""
        return self.get_status_count("skipped")

    @property
    def success_rate(self) -> float:
        """Calculate success rate as percentage."""
        if self.processed_count == 0:
            return 0.0
        return round((self.success_count / self.processed_count) * 100, 2)

    def get_status_count(self, status: str) -> int:
        """Get count of items with specific status."""
        count = 0
        for batch in self.batch_results:
            for result in batch:
                if result.get("status") == status:
                    count += 1
        return count


class BatchStrategy[T](ABC):
    """Abstract base class for batch processing strategies."""

    def __init__(
        self, batch_size: int | None = None, config: ConfigProvider | None = None
    ):
        """Initialize strategy with batch size and config provider."""
        self.config = config
        self.batch_size = batch_size or self._get_default_batch_size()

    @abstractmethod
    def _get_default_batch_size(self) -> int:
        """Get default batch size for this strategy."""

    @abstractmethod
    async def process_batch(self, items: Sequence[T]) -> list[dict]:
        """Process a batch of items according to strategy."""


class ImportStrategy[T](BatchStrategy[T]):
    """Strategy for import operations on individual items."""

    def __init__(
        self,
        processor_func: Callable[[T], Coroutine[Any, Any, dict]],
        batch_size: int | None = None,
        config: ConfigProvider | None = None,
        logger: Logger | None = None,
    ):
        """Initialize import strategy."""
        self.processor_func = processor_func
        self.logger = logger
        super().__init__(batch_size, config)

    def _get_default_batch_size(self) -> int:
        """Get default batch size for import operations."""
        if self.config:
            return self.config.get("DEFAULT_IMPORT_BATCH_SIZE", 50)
        return 50

    async def process_batch(self, items: Sequence[T]) -> list[dict]:
        """Process batch of items for import."""
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
    """Strategy for matching operations with external connectors."""

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
        """Initialize match strategy."""
        self.connector = connector
        self.confidence_threshold = confidence_threshold
        self.connector_type = connector_type
        self.processor_func = processor_func
        self.logger = logger
        super().__init__(batch_size, config)

    def _get_default_batch_size(self) -> int:
        """Get default batch size for matching operations."""
        if self.config and self.connector_type:
            config_key = f"{self.connector_type.upper()}_API_BATCH_SIZE"
            return self.config.get(
                config_key, self.config.get("DEFAULT_MATCH_BATCH_SIZE", 30)
            )
        elif self.config:
            return self.config.get("DEFAULT_MATCH_BATCH_SIZE", 30)
        return 30

    async def process_batch(self, items: Sequence[T]) -> list[dict]:
        """Process batch of items for matching."""
        if self.processor_func:
            try:
                return await self.processor_func(list(items), self.connector)
            except Exception as e:
                if self.logger:
                    self.logger.exception(f"Error in custom match processor: {e}")
                return [{"status": "error", "error": str(e)} for _ in items]

        # No default matching implementation - must provide match_func
        raise NotImplementedError("match_func must be provided for matching operations")


class SyncStrategy[T](BatchStrategy[T]):
    """Strategy for synchronization operations between services."""

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
        """Initialize sync strategy."""
        self.source_service = source_service
        self.target_service = target_service
        self.sync_func = sync_func
        self.connector = connector
        self.logger = logger
        super().__init__(batch_size, config)

    def _get_default_batch_size(self) -> int:
        """Get default batch size for sync operations."""
        if self.config:
            # Use target service config for API rate limiting
            config_key = f"{self.target_service.upper()}_API_BATCH_SIZE"
            return self.config.get(
                config_key, self.config.get("DEFAULT_SYNC_BATCH_SIZE", 20)
            )
        return 20

    async def process_batch(self, items: Sequence[T]) -> list[dict]:
        """Process batch of items for synchronization."""
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
    """Unified batch processor that eliminates duplicate processing patterns.

    Provides configurable strategies for different types of batch operations,
    replacing the scattered batch processing implementations across services.

    Clean Architecture compliant - uses dependency injection for external concerns.
    """

    def __init__(
        self,
        logger: Logger | None = None,
    ):
        """Initialize with injected dependencies."""
        self.logger = logger

    async def process_with_strategy(
        self,
        items: Sequence[T],
        strategy: BatchStrategy[T],
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> BatchResult:
        """Process items using the specified strategy.

        Args:
            items: List of items to process
            strategy: Batch processing strategy to use
            progress_callback: Optional progress callback function

        Returns:
            BatchResult with aggregated processing metrics
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
        """Create an import strategy with the specified processor."""
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
        """Create a match strategy with the specified connector."""
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
        """Create a sync strategy with the specified services."""
        return SyncStrategy(
            source_service=source_service,
            target_service=target_service,
            batch_size=batch_size,
            sync_func=sync_func,
            connector=connector,
            config=config,
            logger=self.logger,
        )
