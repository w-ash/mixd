"""Base connector module providing shared functionality for music service connectors.

This module defines common abstractions and utilities for music service connectors
including standardized metric resolution, batch processing, and error handling.

Key Components:
- BaseMetricResolver: Abstract base class for resolving service-specific metrics
- BatchProcessor: Generic utility for batch processing with concurrency control
- register_metrics: Function to register metric resolvers with the global registry

These components establish a consistent foundation for all connector implementations,
reducing code duplication while enforcing standardized patterns for metric resolution,
error handling, and batch processing operations.
"""

from abc import ABC, abstractmethod
import asyncio
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, ClassVar, TypeVar

from aiolimiter import AsyncLimiter
from attrs import define, field
import backoff

if TYPE_CHECKING:
    from src.domain.entities.playlist import ConnectorPlaylist
    from src.domain.entities.track import ConnectorTrack

from src.config import get_config, get_logger
from src.infrastructure.connectors.metrics_registry import (
    MetricResolverProtocol,
    register_metric_resolver,
)

# Get contextual logger
logger = get_logger(__name__).bind(service="connectors")

# Define type variables for generic operations
T = TypeVar("T")
R = TypeVar("R")


# ConnectorPlaylistItem is now imported from src.domain.entities where needed


@define(frozen=True, slots=True)
class BaseMetricResolver:
    """Base class for resolving service metrics with Clean Architecture compliance.

    Simplified implementation that delegates to MetricsApplicationService
    for all business logic and database operations. Focuses purely on
    field mapping and connector identification.

    Attributes:
        FIELD_MAP: Mapping of metric names to connector metadata fields
        CONNECTOR: Identifier for the connector (overridden by subclasses)
    """

    # To be defined by subclasses - maps metric names to connector metadata fields
    FIELD_MAP: ClassVar[dict[str, str]] = {}

    # Connector name to be overridden by subclasses
    CONNECTOR: ClassVar[str] = ""

    async def resolve(
        self,
        track_ids: list[int],
        metric_name: str,
        uow: Any,  # UnitOfWorkProtocol - avoiding import for infrastructure layer
    ) -> dict[int, Any]:
        """Resolve a metric for multiple tracks using Application Service.

        Delegates to MetricsApplicationService for all business logic
        following Clean Architecture dependency rules.

        Args:
            track_ids: List of internal track IDs to resolve metrics for
            metric_name: Name of the metric to resolve
            uow: UnitOfWork for transaction management

        Returns:
            Dictionary mapping track IDs to their metric values
        """
        # Import at runtime to avoid circular dependencies
        from src.application.services.metrics_application_service import (
            MetricsApplicationService,
        )

        metrics_service = MetricsApplicationService()
        return await metrics_service.resolve_metrics(
            track_ids=track_ids,
            metric_name=metric_name,
            connector=self.CONNECTOR,
            field_map=self.FIELD_MAP,
            uow=uow,
        )


@define(slots=True)
class BaseAPIConnector(ABC):
    """Abstract base class for API connectors with common functionality.

    Provides standardized configuration access, batch processing setup,
    and common patterns for all external service connectors.
    """

    @property
    @abstractmethod
    def connector_name(self) -> str:
        """The name of this connector (e.g., 'spotify', 'lastfm')."""

    @property
    def batch_processor(self) -> "BatchProcessor":
        """Get configured batch processor for this connector's operations."""
        return BatchProcessor(
            batch_size=int(self.get_connector_config("BATCH_SIZE") or 50),
            concurrency_limit=int(self.get_connector_config("CONCURRENCY") or 5),
            retry_count=int(self.get_connector_config("RETRY_COUNT") or 3),
            retry_base_delay=float(
                self.get_connector_config("RETRY_BASE_DELAY") or 1.0
            ),
            retry_max_delay=float(self.get_connector_config("RETRY_MAX_DELAY") or 30.0),
            request_delay=float(self.get_connector_config("REQUEST_DELAY") or 0.1),
            logger_instance=get_logger(__name__).bind(service=self.connector_name),
        )

    def get_connector_config(self, key: str, default=None):
        """Get connector-specific configuration value.

        Args:
            key: Configuration key (without connector prefix)
            default: Default value if key not found

        Returns:
            Configuration value with automatic connector prefixing

        Example:
            self.get_connector_config("BATCH_SIZE") -> get_config("SPOTIFY_API_BATCH_SIZE")
        """
        connector_key = f"{self.connector_name.upper()}_API_{key}"
        return get_config(connector_key, default)

    async def get_playlist(self, playlist_id: str) -> "ConnectorPlaylist":
        """Generic playlist fetcher that delegates to connector-specific methods.

        Automatically delegates to the appropriate connector-specific method based on
        the connector_name property. This provides a consistent interface for all
        playlist-capable connectors while maintaining DRY principles.

        Args:
            playlist_id: The service-specific ID of the playlist to retrieve

        Returns:
            ConnectorPlaylist containing the playlist metadata and tracks

        Raises:
            NotImplementedError: If the connector doesn't support playlist operations
        """
        # Try connector-specific method first (more efficient)
        method_name = f"get_{self.connector_name}_playlist"
        if hasattr(self, method_name):
            method = getattr(self, method_name)
            return await method(playlist_id)

        # Fallback: connector doesn't support playlists
        raise NotImplementedError(
            f"Playlist operations not supported by {self.connector_name} connector"
        )

    def convert_track_to_connector(
        self, track_data: dict[str, Any]
    ) -> "ConnectorTrack":
        """Generic track converter that delegates to connector-specific functions.

        Automatically delegates to the appropriate connector-specific conversion function
        based on the connector_name property. This provides a consistent interface for
        all track conversion operations while maintaining DRY principles.

        Args:
            track_data: Raw track data from the external service API

        Returns:
            ConnectorTrack domain entity with standardized fields

        Raises:
            NotImplementedError: If the connector doesn't support track conversion
        """
        # Import conversion functions dynamically to avoid circular dependencies
        if self.connector_name == "spotify":
            from src.infrastructure.connectors.spotify import (
                convert_spotify_track_to_connector,
            )

            return convert_spotify_track_to_connector(track_data)

        # Fallback: connector doesn't support track conversion
        raise NotImplementedError(
            f"Track conversion not supported by {self.connector_name} connector"
        )


@define(slots=True)
class BatchProcessor[T, R]:
    """Generic batch processor with concurrency control and rate limiting capabilities.

    This utility simplifies batch processing operations across all connectors,
    standardizing concurrency control, rate limiting, batching logic and error handling.
    Uses configuration values from config.py.

    Attributes:
        batch_size: Maximum number of items to process in a single batch
        concurrency_limit: Maximum number of concurrent processing tasks
        retry_count: Maximum number of retry attempts on failure
        retry_base_delay: Base delay between retries (seconds)
        retry_max_delay: Maximum delay between retries (seconds)
        request_delay: Delay between individual requests (seconds)
        rate_limiter: Optional rate limiter for controlling request start rate
        logger_instance: Logger for recording processing events
    """

    batch_size: int
    concurrency_limit: int
    retry_count: int
    retry_base_delay: float
    retry_max_delay: float
    request_delay: float
    rate_limiter: AsyncLimiter | None = field(default=None)
    logger_instance: Any = field(factory=lambda: get_logger(__name__))

    def _on_backoff(self, details):
        """Log backoff event."""
        wait = details["wait"]
        tries = details["tries"]
        target = details["target"].__name__
        args = details["args"]
        kwargs = details["kwargs"]

        self.logger_instance.warning(
            f"Backing off {target} (attempt {tries})",
            retry_delay=f"{wait:.2f}s",
            args=args,
            kwargs=kwargs,
        )

    def _on_giveup(self, details):
        """Log when we give up retrying."""
        target = details["target"].__name__
        tries = details["tries"]
        elapsed = details["elapsed"]
        exception = details.get("exception")

        self.logger_instance.error(
            f"All {tries} attempts failed for {target}",
            elapsed_time=f"{elapsed:.2f}s",
            error=str(exception) if exception else "Unknown error",
            error_type=type(exception).__name__ if exception else "Unknown",
        )

    async def process(
        self,
        items: list[T],
        process_func: Callable[[T], Awaitable[R]],
        progress_callback: Callable[[str, dict], None] | None = None,
        progress_task_name: str = "batch_processing",
        progress_description: str = "Processing items",
    ) -> list[R]:
        """Process items in batches with controlled concurrency and exponential backoff.

        Args:
            items: List of items to process
            process_func: Async function that processes a single item
            progress_callback: Optional callback for progress updates
            progress_task_name: Task name for progress tracking
            progress_description: Human-readable description for progress

        Returns:
            List of results in the same order as input items
        """
        if not items:
            return []

        results: list[R] = []
        semaphore = asyncio.Semaphore(self.concurrency_limit)
        total_batches = (len(items) + self.batch_size - 1) // self.batch_size
        total_items = len(items)
        processed_items = 0

        # Emit batch processing started event
        if progress_callback:
            progress_callback(
                "batch_started",
                {
                    "task_name": progress_task_name,
                    "total_batches": total_batches,
                    "total_items": total_items,
                    "description": progress_description,
                },
            )

        @backoff.on_exception(
            backoff.expo,
            Exception,  # Catch all exceptions - can be customized for specific error types
            max_tries=self.retry_count + 1,  # +1 because first attempt counts
            max_time=None,  # No time limit, just use max_tries
            factor=self.retry_base_delay,
            max_value=self.retry_max_delay,
            jitter=backoff.full_jitter,
            on_backoff=self._on_backoff,
            on_giveup=self._on_giveup,
        )
        async def process_with_backoff(item: T) -> R:
            """Process an item with automatic backoff on failures.

            Uses rate limiter if provided for controlling request start rate,
            or falls back to semaphore-based concurrency limiting.
            """
            if self.rate_limiter:
                # Use rate limiter for controlling request start rate
                async with self.rate_limiter:
                    return await process_func(item)
            else:
                # Fall back to semaphore-based concurrency limiting
                async with semaphore:
                    return await process_func(item)

        # Process in batches for memory efficiency and rate limit management
        for i in range(0, len(items), self.batch_size):
            batch = items[i : i + self.batch_size]
            current_batch = i // self.batch_size + 1
            batch_start_items = processed_items

            self.logger_instance.debug(
                f"Processing batch {current_batch}/{total_batches}",
                batch_size=len(batch),
                total_items=len(items),
            )

            # Emit batch started event
            if progress_callback:
                progress_callback(
                    "batch_progress",
                    {
                        "task_name": progress_task_name,
                        "batch_number": current_batch,
                        "total_batches": total_batches,
                        "batch_size": len(batch),
                        "items_processed": processed_items,
                        "total_items": total_items,
                        "description": f"{progress_description} (batch {current_batch}/{total_batches})",
                    },
                )

            # Create a progress-aware wrapper for individual item processing
            async def process_item_with_progress(
                item: T,
                item_index: int,
                batch_start: int = batch_start_items,
                batch_num: int = current_batch,
            ) -> R:
                """Process item and emit progress event."""
                result = await process_with_backoff(item)

                # Emit individual item progress based on config frequency
                current_item = batch_start + item_index + 1
                progress_frequency = get_config("BATCH_PROGRESS_LOG_FREQUENCY") or 10
                if progress_callback and (
                    current_item % progress_frequency == 0
                    or current_item == total_items
                ):
                    # Try to get a meaningful description from the item
                    item_desc = ""
                    try:
                        if hasattr(item, "title") and hasattr(item, "artists"):
                            artists = getattr(item, "artists", [])
                            if artists and hasattr(artists[0], "name"):
                                artist_name = artists[0].name
                            else:
                                artist_name = "Unknown Artist"
                            item_desc = f"{artist_name} - {getattr(item, 'title', 'Unknown Track')}"
                        elif hasattr(item, "name"):
                            item_desc = str(getattr(item, "name", ""))
                        elif hasattr(item, "id"):
                            item_desc = f"Item {getattr(item, 'id', '')}"
                    except (AttributeError, IndexError):
                        # Fallback if item structure is unexpected
                        item_desc = f"Item {item_index + 1}"

                    progress_callback(
                        "track_processed",
                        {
                            "task_name": progress_task_name,
                            "items_processed": current_item,
                            "total_items": total_items,
                            "current_batch": batch_num,
                            "item_description": item_desc,
                            "description": f"Processed {current_item}/{total_items} items",
                        },
                    )

                return result

            # Create tasks for all items in this batch
            batch_tasks = [
                asyncio.create_task(process_item_with_progress(item, idx))
                for idx, item in enumerate(batch)
            ]

            # Process items as they complete for real-time progress (streaming pattern)
            valid_results = []
            completed_in_batch = 0

            for completed_task in asyncio.as_completed(batch_tasks):
                try:
                    result = await completed_task
                    valid_results.append(result)
                    completed_in_batch += 1

                    # Log real-time completion within batch
                    self.logger_instance.debug(
                        f"Item {completed_in_batch}/{len(batch)} completed in batch {current_batch}",
                        batch_progress=f"{completed_in_batch}/{len(batch)}",
                        total_progress=f"{processed_items + completed_in_batch}/{total_items}",
                    )

                except Exception as result:
                    self.logger_instance.error(
                        "Item processing failed",
                        error=str(result),
                        error_type=type(result).__name__,
                    )

            results.extend(valid_results)
            processed_items += len(batch)

            # Emit batch completed event
            if progress_callback:
                progress_callback(
                    "batch_completed",
                    {
                        "task_name": progress_task_name,
                        "batch_number": current_batch,
                        "total_batches": total_batches,
                        "items_processed": processed_items,
                        "total_items": total_items,
                        "batch_results": len(valid_results),
                        "batch_failures": len(batch) - len(valid_results),
                    },
                )

            # Log batch completion
            self.logger_instance.debug(
                f"Batch {current_batch} complete",
                valid_results=len(valid_results),
                failures=len(batch) - len(valid_results),
            )

        return results


def register_metrics(
    metric_resolver: MetricResolverProtocol,
    field_map: dict[str, str],
) -> None:
    """Register all metrics defined in field_map with the given resolver.

    Args:
        metric_resolver: The resolver instance to register
        field_map: Mapping of metric names to connector fields
    """
    for metric_name in field_map:
        register_metric_resolver(metric_name, metric_resolver)
