"""Import-specific batch processor for file processing and data import operations.

Provides specialized batch processing for import operations with:
- Memory-efficient chunking for large files
- Progress tracking optimized for import operations
- Error aggregation across batches
- No API rate limiting (not needed for file processing)
- Simple retry logic for transient processing errors

This processor is specifically designed for file/data import operations and should NOT be used for
external API calls, database operations, or other specialized batch operations.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from attrs import define, field

from src.config import get_logger, settings

# Get contextual logger
logger = get_logger(__name__).bind(service="import_batch_processor")

# Define type variables for generic operations
T = TypeVar("T")
R = TypeVar("R")


@define(slots=True)
class ImportBatchProcessor[T, R]:
    """Processes file/data import operations in batches with memory management.

    Designed specifically for processing large files, transforming records, and import operations.
    Optimized for memory efficiency and progress tracking without API-specific features like
    rate limiting or complex concurrency control.

    DO NOT USE for external API calls, database operations, or simple chunking.
    Use DatabaseBatchProcessor or SimpleBatchProcessor instead.

    Args:
        batch_size: Items per batch (controls memory usage)
        retry_count: Max retry attempts for transient errors
        retry_base_delay: Starting delay between retries (seconds)
        memory_limit_mb: Approximate memory limit per batch in MB (advisory)
        logger_instance: Logger for progress and error reporting
    """

    batch_size: int
    retry_count: int = field(default=3)
    retry_base_delay: float = field(default=1.0)
    memory_limit_mb: int = field(default=100)  # Advisory memory limit per batch
    logger_instance: Any = field(factory=lambda: get_logger(__name__))

    def __attrs_post_init__(self):
        """Validate configuration for import operations."""
        if self.batch_size > settings.import_settings.memory_warning_threshold:
            self.logger_instance.warning(
                f"Large batch size {self.batch_size} may cause memory issues. "
                "Consider using smaller batch sizes (100-1000) for import operations."
            )

    async def process(
        self,
        items: list[T],
        process_func: Callable[[list[T]], Awaitable[R]],
        progress_callback: Callable[[str, dict], None] | None = None,
        progress_task_name: str = "import_batch_processing",
        progress_description: str = "Processing import data",
    ) -> list[R]:
        """Process items in batches optimized for import operations.

        Processes items in memory-efficient batches with progress tracking and error
        aggregation. Uses minimal concurrency to avoid overwhelming the system during
        large import operations.

        Args:
            items: Items to process
            process_func: Async function that processes one batch
            progress_callback: Optional function to receive progress events
            progress_task_name: Identifier for progress tracking
            progress_description: Human-readable task description

        Returns:
            Results from each batch in order (failed batches have error info)
        """
        if not items:
            return []

        results: list[R] = []
        total_batches = (len(items) + self.batch_size - 1) // self.batch_size
        total_items = len(items)
        processed_items = 0

        # Track errors across batches for reporting
        error_summary = {"total_errors": 0, "error_types": {}, "failed_batches": []}

        # Emit batch processing started event
        if progress_callback:
            progress_callback(
                "batch_started",
                {
                    "task_name": progress_task_name,
                    "total_batches": total_batches,
                    "total_items": total_items,
                    "description": progress_description,
                    "estimated_memory_mb": (total_batches * self.memory_limit_mb),
                },
            )

        self.logger_instance.info(
            f"Starting import batch processing: {total_items} items in {total_batches} batches "
            f"(~{self.memory_limit_mb}MB per batch)"
        )

        # Process in batches with minimal concurrency for import operations
        for i in range(0, len(items), self.batch_size):
            batch = items[i : i + self.batch_size]
            current_batch = i // self.batch_size + 1

            self.logger_instance.debug(
                f"Processing import batch {current_batch}/{total_batches}",
                batch_size=len(batch),
                total_items=total_items,
            )

            # Emit batch progress event with import-specific details
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
                        "errors_so_far": error_summary["total_errors"],
                    },
                )

            # Process batch with retry logic for transient errors
            batch_result = None
            batch_error = None

            for attempt in range(self.retry_count + 1):
                try:
                    batch_result = await process_func(batch)
                    break  # Success, exit retry loop

                except Exception as e:
                    batch_error = e
                    is_final_attempt = attempt == self.retry_count

                    # Check if this is a retriable error (not file format errors, etc.)
                    error_msg = str(e).lower()
                    is_retriable = (
                        "timeout" in error_msg
                        or "connection" in error_msg
                        or "temporary" in error_msg
                        or "busy" in error_msg
                    )

                    if is_retriable and not is_final_attempt:
                        delay = self.retry_base_delay * (
                            2**attempt
                        )  # Exponential backoff
                        self.logger_instance.warning(
                            f"Import batch {current_batch} failed (attempt {attempt + 1}), "
                            f"retrying in {delay}s: {e}"
                        )
                        await asyncio.sleep(delay)
                    else:
                        # Log permanent failure
                        self.logger_instance.error(
                            f"Import batch {current_batch} failed permanently: {e}",
                            error_type=type(e).__name__,
                            is_retriable=is_retriable,
                            final_attempt=is_final_attempt,
                            batch_size=len(batch),
                        )

                        # Track error for summary
                        error_type = type(e).__name__
                        error_summary["total_errors"] += 1
                        error_summary["error_types"][error_type] = (
                            error_summary["error_types"].get(error_type, 0) + 1
                        )
                        error_summary["failed_batches"].append({
                            "batch_number": current_batch,
                            "error": str(e),
                            "error_type": error_type,
                            "batch_size": len(batch),
                        })
                        break

            # Add results (successful or error info)
            if batch_result is not None:
                results.append(batch_result)
                self.logger_instance.debug(
                    f"Import batch {current_batch} completed successfully"
                )
            else:
                # Add error result to maintain batch ordering
                error_result = {
                    "status": "error",
                    "batch_number": current_batch,
                    "error": str(batch_error) if batch_error else "Unknown error",
                    "batch_size": len(batch),
                }
                results.append(error_result)  # type: ignore

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
                        "error_summary": error_summary,
                    },
                )

        # Final summary logging
        success_count = sum(
            1
            for r in results
            if not (isinstance(r, dict) and r.get("status") == "error")
        )
        failure_count = total_batches - success_count

        self.logger_instance.info(
            f"Import batch processing completed: {success_count}/{total_batches} batches successful, "
            f"{processed_items} total items processed"
        )

        if error_summary["total_errors"] > 0:
            self.logger_instance.warning(
                f"Import errors encountered: {error_summary['total_errors']} errors "
                f"across {failure_count} batches",
                error_types=error_summary["error_types"],
            )

        return results
