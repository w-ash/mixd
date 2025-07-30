"""Base classes for music service API connectors.

Provides shared functionality for integrating with external music services like Spotify,
Last.fm, MusicBrainz, etc. Child connectors inherit from these base classes to get
standardized configuration loading, batch processing, and metric resolution.

Classes:
    BaseMetricResolver: Retrieves track metrics (popularity, play counts) from connector metadata
    BaseAPIConnector: Abstract base for service-specific API clients (inherit for Spotify, Last.fm)
    BatchProcessor: Processes large lists with automatic retries and rate limiting

Functions:
    register_metrics: Register metric resolvers for use by the application layer

Example:
    ```python
    class SpotifyConnector(BaseAPIConnector):
        @property
        def connector_name(self) -> str:
            return "spotify"
        # Implement service-specific methods...
    ```
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
    """Retrieves track metrics from connector metadata stored in database.

    Looks up track metrics like Spotify popularity or Last.fm play counts by querying
    the connector_metadata table. Child classes define which metadata fields map to
    which metric names via FIELD_MAP and CONNECTOR class variables.

    Attributes:
        FIELD_MAP: Maps metric names to connector metadata field names
        CONNECTOR: Service identifier (e.g., "spotify", "lastfm")
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
        """Retrieve metric values for multiple tracks from database.

        Args:
            track_ids: Internal track IDs to get metrics for
            metric_name: Name of metric to retrieve (e.g., "spotify_popularity")
            uow: Database unit of work for transaction management

        Returns:
            Track ID to metric value mapping
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
    """Abstract base for music service API clients.

    Inherit from this class to create connectors for specific services like Spotify, Last.fm,
    MusicBrainz, etc. Provides common configuration loading, batch processing setup, and
    delegation patterns for playlist/track operations.

    Child classes must implement:
        - connector_name property (returns service name like "spotify")
        - Service-specific API methods as needed

    Automatically provides:
        - Configuration loading with service-specific prefixes
        - Pre-configured batch processor with retry logic
        - Generic playlist/track conversion that delegates to service methods
    """

    @property
    @abstractmethod
    def connector_name(self) -> str:
        """Service identifier for this connector (e.g., 'spotify', 'lastfm')."""

    @property
    def batch_processor(self) -> "BatchProcessor":
        """Get pre-configured batch processor with service-specific settings."""
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
        """Load configuration value with automatic service-specific prefixing.

        Args:
            key: Configuration key without service prefix
            default: Fallback value if key not found

        Returns:
            Configuration value from environment/config files

        Example:
            If connector_name="spotify" and key="BATCH_SIZE":
            Looks up "SPOTIFY_API_BATCH_SIZE" in configuration
        """
        connector_key = f"{self.connector_name.upper()}_API_{key}"
        return get_config(connector_key, default)

    async def get_playlist(self, playlist_id: str) -> "ConnectorPlaylist":
        """Fetch playlist from service by delegating to service-specific method.

        Automatically calls the appropriate method based on connector_name:
        - spotify connector -> calls get_spotify_playlist()
        - lastfm connector -> calls get_lastfm_playlist()
        - etc.

        Args:
            playlist_id: Service-specific playlist identifier

        Returns:
            Playlist with tracks converted to standard format

        Raises:
            NotImplementedError: If service doesn't support playlists
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
        """Convert raw API track data to standardized ConnectorTrack format.

        Delegates to service-specific conversion functions based on connector_name.
        Currently supports Spotify; extensible to other services.

        Args:
            track_data: Raw track data from service API response

        Returns:
            Standardized track object with normalized fields

        Raises:
            NotImplementedError: If service doesn't have conversion function
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
    """Processes large lists with automatic retries, rate limiting, and progress tracking.

    Splits work into batches to avoid memory issues and API rate limits. Automatically
    retries failed items with exponential backoff. Emits progress events for UI updates.

    Args:
        batch_size: Items per batch (prevents memory issues)
        concurrency_limit: Max concurrent operations (respects API limits)
        retry_count: Max retry attempts per failed item
        retry_base_delay: Starting delay between retries (seconds)
        retry_max_delay: Maximum delay between retries (seconds)
        request_delay: Minimum delay between requests (seconds)
        rate_limiter: Optional external rate limiter
        logger_instance: Logger for progress and error reporting
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
        """Log retry attempt with delay information."""
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
        """Log final failure after all retry attempts exhausted."""
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
        """Process items in batches with automatic retries and progress tracking.

        Splits items into batches, processes each batch concurrently while respecting
        rate limits, retries failures with exponential backoff, and emits progress
        events for UI updates.

        Args:
            items: Items to process
            process_func: Async function that processes one item
            progress_callback: Optional function to receive progress events
            progress_task_name: Identifier for progress tracking
            progress_description: Human-readable task description

        Returns:
            Results in same order as input items (failed items excluded)
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
            """Process item with automatic retry on failure and rate limiting."""
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
                """Process item and emit progress events for UI updates."""
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
    """Register metric resolver for all metrics defined in field_map.

    Connects metric names to resolver instances so the application layer can
    look up track metrics like popularity or play counts.

    Args:
        metric_resolver: Resolver instance that can fetch metric values
        field_map: Maps metric names to connector metadata field names
    """
    for metric_name in field_map:
        register_metric_resolver(metric_name, metric_resolver)
