"""API-specific batch processor for external service operations.

Provides specialized batch processing for external API calls with:
- Sequential batch processing with concurrency control
- Rate limiting with AsyncLimiter integration
- Exponential backoff with configurable retry delays
- Progress tracking optimized for API operations

This processor is specifically designed for external API operations and should NOT be used for
database operations, file processing, or other internal batch operations.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from aiolimiter import AsyncLimiter
from attrs import define, field

from src.config import get_logger, settings
from src.infrastructure.connectors._shared.retry_wrapper import RetryWrapper

# Get contextual logger
logger = get_logger(__name__).bind(service="api_batch_processor")

# Define type variables for generic operations
T = TypeVar("T")
R = TypeVar("R")


@define(slots=True)
class APIBatchProcessor[T, R]:
    """Processes external API operations in batches with rate limiting and retries.

    Designed specifically for external service API calls like Spotify, Last.fm, MusicBrainz.
    Provides automatic retries with exponential backoff, rate limiting via AsyncLimiter,
    and progress tracking optimized for API operations.

    DO NOT USE for database operations, file processing, or other internal batch operations.
    Use DatabaseBatchProcessor, ImportBatchProcessor, or SimpleBatchProcessor instead.

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
    _retry_wrapper: RetryWrapper = field(init=False)

    def __attrs_post_init__(self):
        """Initialize retry wrapper with configured parameters."""
        object.__setattr__(self, '_retry_wrapper', RetryWrapper(
            retry_count=self.retry_count,
            retry_base_delay=self.retry_base_delay,
            retry_max_delay=self.retry_max_delay,
            logger_instance=self.logger_instance,
        ))

    async def process(
        self,
        items: list[T],
        process_func: Callable[[T], Awaitable[R]],
        progress_callback: Callable[[str, dict], None] | None = None,
        progress_task_name: str = "api_batch_processing",
        progress_description: str = "Processing API requests",
    ) -> list[R]:
        """Process items in sequential batches with automatic retries and progress tracking.

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

        async def process_with_rate_limit_and_retry(item: T) -> R:
            """Process item with rate limiting and automatic retry on failure."""
            
            async def rate_limited_call() -> R:
                """Apply rate limiting and concurrency control."""
                # Rate limit before API call using AsyncLimiter (concurrent-safe)
                if self.rate_limiter:
                    await self.rate_limiter.acquire()
                
                # Use semaphore for concurrency control
                async with semaphore:
                    return await process_func(item)
            
            # Use centralized retry wrapper
            retry_wrapped_call = self._retry_wrapper.with_exponential_backoff(rate_limited_call)
            return await retry_wrapped_call()

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

            # Create a simplified progress-aware wrapper
            async def process_item_with_progress(
                item: T,
                item_index: int,
                batch_start: int = batch_start_items,
                batch_num: int = current_batch,
            ) -> R:
                """Process item and emit periodic progress events."""
                result = await process_with_rate_limit_and_retry(item)

                # Emit progress at configured intervals
                current_item = batch_start + item_index + 1
                progress_frequency = settings.batch.progress_log_frequency
                if progress_callback and (
                    current_item % progress_frequency == 0
                    or current_item == total_items
                ):
                    progress_callback(
                        "item_processed",
                        {
                            "task_name": progress_task_name,
                            "items_processed": current_item,
                            "total_items": total_items,
                            "current_batch": batch_num,
                            "description": f"Processed {current_item}/{total_items} items",
                        },
                    )

                return result

            # Create all tasks immediately using standard asyncio patterns
            batch_tasks = [
                asyncio.create_task(process_item_with_progress(item, idx))
                for idx, item in enumerate(batch)
            ]

            self.logger_instance.debug(
                f"Created {len(batch_tasks)} concurrent tasks for batch {current_batch}",
                rate_per_second=self.rate_limiter.max_rate if self.rate_limiter else "unlimited",
            )

            # Process items as they complete for real-time progress
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

                except Exception as e:
                    completed_in_batch += 1
                    self.logger_instance.error(
                        f"Item {completed_in_batch} failed in batch {current_batch}",
                        error=str(e),
                        error_type=type(e).__name__,
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