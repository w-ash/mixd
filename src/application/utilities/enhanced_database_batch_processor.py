"""Enhanced database batch processor using the new event-driven progress system.

This demonstrates how to migrate from callback-based progress tracking to the new
event-driven system. Provides the same functionality as DatabaseBatchProcessor
but with better progress tracking and user feedback.
"""

# pyright: reportAny=false, reportExplicitAny=false

import asyncio
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from attrs import define, field

from src.config import get_logger

if TYPE_CHECKING:
    from structlog.stdlib import BoundLogger

from src.config.constants import BusinessLimits
from src.domain.entities.progress import (
    NullProgressEmitter,
    OperationStatus,
    ProgressEmitter,
    create_progress_event,
    create_progress_operation,
)

# Get contextual logger
logger = get_logger(__name__).bind(service="enhanced_database_batch_processor")


@define(slots=True)
class EnhancedDatabaseBatchProcessor[T, R]:
    """Enhanced database batch processor with event-driven progress tracking.

    Uses the new progress system instead of callbacks for better user experience.
    Provides the same database-optimized processing as DatabaseBatchProcessor
    but with Rich progress bars, better error handling, and consistent UX.

    Migration from DatabaseBatchProcessor:
    - Remove progress_callback parameter
    - Add progress_emitter parameter (optional)
    - Progress is automatically displayed via registered providers
    - Better error reporting and status tracking

    Args:
        batch_size: Items per batch (manages memory and enables progress tracking)
        retry_count: Max retry attempts for database deadlocks
        retry_base_delay: Starting delay between retries (seconds)
        progress_emitter: Progress emitter for event-driven tracking (optional)
        logger_instance: Logger for progress and error reporting
    """

    batch_size: int
    retry_count: int = field(default=3)
    retry_base_delay: float = field(default=1.0)
    progress_emitter: ProgressEmitter = field(factory=NullProgressEmitter)
    logger_instance: BoundLogger = field(factory=lambda: get_logger(__name__))

    def __attrs_post_init__(self):
        """Validate configuration."""
        if self.batch_size > BusinessLimits.BATCH_WARNING_THRESHOLD:
            self.logger_instance.warning(
                f"Large batch size {self.batch_size} may increase memory usage. Consider smaller batch sizes (10-50) for database operations."
            )

    async def process(
        self,
        items: list[T],
        process_func: Callable[[list[T]], Awaitable[R]],
        operation_description: str = "Processing database operations",
        **operation_metadata: Any,
    ) -> list[R]:
        """Process items in batches with enhanced progress tracking.

        Creates a progress operation, processes items sequentially to prevent
        database locks, and provides real-time progress updates to users.

        Args:
            items: Items to process
            process_func: Async function that processes one batch
            operation_description: Human-readable description of the operation
            **operation_metadata: Additional context for the operation

        Returns:
            Results from each batch in order (failed batches excluded)
        """
        if not items:
            return []

        total_batches = (len(items) + self.batch_size - 1) // self.batch_size
        total_items = len(items)

        # Create and start progress operation
        operation = create_progress_operation(
            description=operation_description,
            total_items=total_items,
            batch_size=self.batch_size,
            total_batches=total_batches,
            processor_type="enhanced_database",
            **operation_metadata,
        )

        operation_id = await self.progress_emitter.start_operation(operation)

        try:
            results: list[R] = []
            processed_items = 0

            self.logger_instance.info(
                f"Starting enhanced database batch processing: {total_items} items in {total_batches} batches"
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

                # Emit progress event for batch start
                await self.progress_emitter.emit_progress(
                    create_progress_event(
                        operation_id=operation_id,
                        current=processed_items,
                        total=total_items,
                        message=f"Processing batch {current_batch}/{total_batches}",
                        current_batch=current_batch,
                        total_batches=total_batches,
                    )
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
                                + f"retrying in {delay}s: {e}"
                            )

                            # Emit progress event for retry
                            await self.progress_emitter.emit_progress(
                                create_progress_event(
                                    operation_id=operation_id,
                                    current=processed_items,
                                    total=total_items,
                                    message=f"Retrying batch {current_batch} (attempt {attempt + 2})",
                                    retry_attempt=attempt + 1,
                                    error_message=str(e),
                                )
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

                # Add successful results and update progress
                if batch_result is not None:
                    results.append(batch_result)
                    processed_items += len(batch)

                    self.logger_instance.debug(
                        f"Database batch {current_batch} completed successfully"
                    )

                    # Emit progress event for batch completion
                    await self.progress_emitter.emit_progress(
                        create_progress_event(
                            operation_id=operation_id,
                            current=processed_items,
                            total=total_items,
                            message=f"Completed batch {current_batch}/{total_batches}",
                            batches_completed=current_batch,
                            batches_successful=len(results),
                        )
                    )
                else:
                    # Still update processed count for progress tracking even on failure
                    processed_items += len(batch)

                    # Emit progress event for batch failure
                    await self.progress_emitter.emit_progress(
                        create_progress_event(
                            operation_id=operation_id,
                            current=processed_items,
                            total=total_items,
                            message=f"Failed batch {current_batch}/{total_batches}",
                            batches_completed=current_batch,
                            batches_failed=(current_batch - len(results)),
                        )
                    )

            success_count = len(results)
            failure_count = total_batches - success_count

            self.logger_instance.info(
                f"Enhanced database batch processing completed: {success_count}/{total_batches} batches successful"
            )

            if failure_count > 0:
                self.logger_instance.warning(
                    f"{failure_count} database batches failed and were skipped"
                )

            # Complete operation successfully
            await self.progress_emitter.complete_operation(
                operation_id, OperationStatus.COMPLETED
            )

        except Exception as e:
            # Complete operation with failure status
            self.logger_instance.error(
                f"Enhanced database batch processing failed: {e}",
                error_type=type(e).__name__,
            )

            await self.progress_emitter.complete_operation(
                operation_id, OperationStatus.FAILED
            )
            raise
        else:
            return results
