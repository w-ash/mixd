"""Database-specific batch processor for bulk database operations.

Provides specialized batch processing for database operations with:
- Sequential processing to prevent SQLite locks
- Transaction boundary management
- No API rate limiting (not needed for database operations)
- Optimized for database bulk operations like inserts, updates
- Simple retry logic for database deadlock scenarios

This processor is specifically designed for database operations and should NOT be used for
external API calls, file processing, or other operations requiring rate limiting.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from attrs import define, field

from src.config import get_logger
from src.config.constants import BusinessLimits

# Get contextual logger
logger = get_logger(__name__).bind(service="database_batch_processor")

# Define type variables for generic operations
T = TypeVar("T")
R = TypeVar("R")


@define(slots=True)
class DatabaseBatchProcessor[T, R]:
    """Processes database bulk operations in batches with transaction safety.

    Designed specifically for database operations like bulk inserts, updates, and saves.
    Uses sequential processing to prevent SQLite locks and provides transaction-aware
    error handling without API-specific features like rate limiting.

    DO NOT USE for external API calls, file processing, or other non-database operations.
    Use ImportBatchProcessor or SimpleBatchProcessor instead.

    Args:
        batch_size: Items per batch (prevents SQLite lock issues)
        retry_count: Max retry attempts for database deadlocks
        retry_base_delay: Starting delay between retries (seconds)
        logger_instance: Logger for progress and error reporting
    """

    batch_size: int
    retry_count: int = field(default=3)
    retry_base_delay: float = field(default=1.0)
    logger_instance: Any = field(factory=lambda: get_logger(__name__))

    def __attrs_post_init__(self):
        """Validate configuration for database operations."""
        if self.batch_size > BusinessLimits.SQLITE_BATCH_WARNING_THRESHOLD:
            self.logger_instance.warning(
                f"Large batch size {self.batch_size} may cause SQLite locks. "
                "Consider using smaller batch sizes (10-50) for database operations."
            )

    async def process(
        self,
        items: list[T],
        process_func: Callable[[list[T]], Awaitable[R]],
        progress_callback: Callable[[str, dict], None] | None = None,
        progress_task_name: str = "database_batch_processing",
        progress_description: str = "Processing database operations",
    ) -> list[R]:
        """Process items in batches with database-optimized handling.

        Processes items sequentially to prevent database locks, with simple retry
        logic for deadlock scenarios. No rate limiting or API-specific features.

        Args:
            items: Items to process
            process_func: Async function that processes one batch
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

        self.logger_instance.info(
            f"Starting database batch processing: {total_items} items in {total_batches} batches"
        )

        # Process in batches sequentially (no concurrency for database operations)
        for i in range(0, len(items), self.batch_size):
            batch = items[i : i + self.batch_size]
            current_batch = i // self.batch_size + 1

            self.logger_instance.debug(
                f"Processing database batch {current_batch}/{total_batches}",
                batch_size=len(batch),
                total_items=total_items,
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

            # Process batch with simple retry for database deadlocks
            batch_result = None
            for attempt in range(self.retry_count + 1):
                try:
                    batch_result = await process_func(batch)
                    break  # Success, exit retry loop

                except Exception as e:
                    is_final_attempt = attempt == self.retry_count
                    error_msg = str(e).lower()

                    # Check if this is a retriable database error
                    is_retriable = (
                        "database is locked" in error_msg
                        or "deadlock" in error_msg
                        or "busy" in error_msg
                    )

                    if is_retriable and not is_final_attempt:
                        delay = self.retry_base_delay * (
                            2**attempt
                        )  # Exponential backoff
                        self.logger_instance.warning(
                            f"Database batch {current_batch} failed (attempt {attempt + 1}), "
                            f"retrying in {delay}s: {e}"
                        )
                        await asyncio.sleep(delay)
                    else:
                        self.logger_instance.error(
                            f"Database batch {current_batch} failed permanently: {e}",
                            error_type=type(e).__name__,
                            is_retriable=is_retriable,
                            final_attempt=is_final_attempt,
                        )
                        batch_result = None
                        break  # Give up on this batch

            # Add successful results
            if batch_result is not None:
                results.append(batch_result)
                processed_items += len(batch)

                self.logger_instance.debug(
                    f"Database batch {current_batch} completed successfully"
                )
            else:
                # Still update processed count for progress tracking even on failure
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
                        "batch_results": 1 if batch_result is not None else 0,
                        "batch_failures": 0 if batch_result is not None else 1,
                    },
                )

        success_count = len(results)
        failure_count = total_batches - success_count

        self.logger_instance.info(
            f"Database batch processing completed: {success_count}/{total_batches} batches successful"
        )

        if failure_count > 0:
            self.logger_instance.warning(
                f"{failure_count} database batches failed and were skipped"
            )

        return results
