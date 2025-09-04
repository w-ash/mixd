"""Simple batch processor for basic chunking operations.

Provides minimal batch processing for simple operations with:
- Basic item chunking into batches
- Minimal overhead and maximum performance
- No retry logic, rate limiting, or complex features
- Sequential or concurrent processing options
- Simple progress tracking

This processor is designed for lightweight batch operations and should NOT be used for
external API calls, database operations, or complex import operations requiring specialized features.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from attrs import define, field

from src.config import get_logger

# Get contextual logger
logger = get_logger(__name__).bind(service="simple_batch_processor")

# Define type variables for generic operations
T = TypeVar("T")
R = TypeVar("R")


@define(slots=True)
class SimpleBatchProcessor[T, R]:
    """Processes items in simple batches with minimal overhead.

    Designed for basic batch operations that just need items split into chunks.
    Provides optional concurrency control and basic progress tracking without
    specialized features like retry logic, rate limiting, or error classification.

    Use this for simple utility operations. For specialized needs, use:
    - DatabaseBatchProcessor: Database operations with transaction safety
    - ImportBatchProcessor: File processing with memory management

    Args:
        batch_size: Items per batch
        max_concurrency: Max concurrent batches (1 = sequential, None = unlimited)
        logger_instance: Logger for basic progress reporting
    """

    batch_size: int
    max_concurrency: int | None = field(default=1)  # Sequential by default
    logger_instance: Any = field(factory=lambda: get_logger(__name__))

    async def process(
        self,
        items: list[T],
        process_func: Callable[[list[T]], Awaitable[R]],
        progress_callback: Callable[[str, dict], None] | None = None,
        progress_task_name: str = "simple_batch_processing",
        progress_description: str = "Processing items",
    ) -> list[R]:
        """Process items in simple batches.

        Splits items into batches and processes them with optional concurrency control.
        No retry logic, rate limiting, or complex error handling - just basic batching.

        Args:
            items: Items to process
            process_func: Async function that processes one batch of items
            progress_callback: Optional function to receive progress events
            progress_task_name: Identifier for progress tracking
            progress_description: Human-readable task description

        Returns:
            Results from each batch in order (failed batches excluded)
        """
        if not items:
            return []

        results: list[R] = []
        total_batches = (len(items) + self.batch_size - 1) // self.batch_size
        total_items = len(items)
        processed_batches = 0

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

        self.logger_instance.debug(
            f"Starting simple batch processing: {total_items} items in {total_batches} batches"
        )

        # Split items into batches
        batches = [
            items[i : i + self.batch_size]
            for i in range(0, len(items), self.batch_size)
        ]

        # Process batches with optional concurrency control
        if self.max_concurrency == 1:
            # Sequential processing
            for batch_num, batch in enumerate(batches, 1):
                try:
                    result = await process_func(batch)
                    results.append(result)
                    processed_batches += 1

                    self.logger_instance.debug(
                        f"Simple batch {batch_num}/{total_batches} completed"
                    )

                    # Emit progress event
                    if progress_callback:
                        progress_callback(
                            "batch_completed",
                            {
                                "task_name": progress_task_name,
                                "batch_number": batch_num,
                                "total_batches": total_batches,
                                "batches_processed": processed_batches,
                                "total_items": total_items,
                                "description": f"{progress_description} ({batch_num}/{total_batches})",
                            },
                        )

                except Exception as e:
                    self.logger_instance.error(
                        f"Simple batch {batch_num} failed: {e}",
                        error_type=type(e).__name__,
                        batch_size=len(batch),
                    )
                    # Continue with next batch - no retry logic

        else:
            # Concurrent processing with semaphore
            semaphore = asyncio.Semaphore(self.max_concurrency or len(batches))

            async def process_batch_with_semaphore(
                batch_num: int, batch: list[T]
            ) -> R | None:
                async with semaphore:
                    try:
                        result = await process_func(batch)
                        self.logger_instance.debug(
                            f"Concurrent batch {batch_num}/{total_batches} completed"
                        )
                        return result
                    except Exception as e:
                        self.logger_instance.error(
                            f"Concurrent batch {batch_num} failed: {e}",
                            error_type=type(e).__name__,
                            batch_size=len(batch),
                        )
                        return None

            # Create tasks for all batches
            tasks = [
                asyncio.create_task(process_batch_with_semaphore(i + 1, batch))
                for i, batch in enumerate(batches)
            ]

            # Wait for all tasks to complete
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)

            # Filter successful results
            for i, result in enumerate(batch_results):
                if result is not None and not isinstance(result, Exception):
                    results.append(result)  # type: ignore  # We've checked it's not an Exception
                    processed_batches += 1

                    # Emit progress event for successful batches
                    if progress_callback:
                        progress_callback(
                            "batch_completed",
                            {
                                "task_name": progress_task_name,
                                "batch_number": i + 1,
                                "total_batches": total_batches,
                                "batches_processed": processed_batches,
                                "total_items": total_items,
                                "description": f"{progress_description} ({i + 1}/{total_batches})",
                            },
                        )

        success_count = len(results)
        failure_count = total_batches - success_count

        self.logger_instance.debug(
            f"Simple batch processing completed: {success_count}/{total_batches} batches successful"
        )

        if failure_count > 0:
            self.logger_instance.warning(
                f"{failure_count} simple batches failed and were skipped"
            )

        return results
