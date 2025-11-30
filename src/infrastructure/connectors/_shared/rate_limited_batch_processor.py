"""Generic queue-based batch processor with rate limiting for any connector.

This module provides a "conveyor belt" architecture for processing batches of items
with controlled rate limiting while maintaining full concurrency. Items are launched
at steady intervals regardless of response times, with results processed immediately
as they complete.

Key features:
- Work queue buffers ready-to-run calls (originals + retries)
- Background limiter loop launches requests at steady intervals (e.g. every 200ms for 5/sec)
- Multiple requests execute concurrently (slow responses don't block new launches)
- Results processed immediately via asyncio.as_completed()
- Failed requests automatically rejoin the controlled stream with retry logic
- Comprehensive logging for API call tracing and debugging

Example:
    ```python
    processor = RateLimitedBatchProcessor(
        rate_per_second=settings.api.lastfm_rate_limit,
        connector_name="lastfm",
        max_concurrent_tasks=settings.api.lastfm_concurrency,
    )

    async for result in processor.process_batch(tracks, api_call_func):
        handle_result(result)
    ```
"""

import asyncio
from collections.abc import AsyncIterator, Callable
import contextlib
import time
from typing import Any, TypeVar
import uuid

from attrs import define, field

from src.config import get_logger

# Type variables for generic processing
TItem = TypeVar("TItem")  # Input item type (Track, etc.)
TResult = TypeVar("TResult")  # Result type (metadata dict, etc.)

logger = get_logger(__name__).bind(service="rate_limited_batch_processor")

# Constants for result processing
_RESULT_POLLING_INTERVAL_MS = 10  # Milliseconds between result collection checks


@define(frozen=True, slots=True)
class WorkItem:
    """Individual work item with tracking metadata."""

    item_id: str
    item: Any
    queued_at: float = field(factory=time.time)


@define(slots=False)  # Disable slots to allow dynamic attribute assignment
class RateLimitedBatchProcessor:
    """Generic queue-based batch processor with rate limiting for any connector.

    Implements a "conveyor belt" pattern where items are launched at controlled
    intervals while allowing full concurrency for execution and result processing.

    Args:
        rate_per_second: Maximum requests to launch per second (from settings.api.{connector}_rate_limit)
        connector_name: Name for logging and identification
        max_concurrent_tasks: Maximum number of tasks running simultaneously (from settings.api.{connector}_concurrency)

    Note: Retry logic is handled by the @resilient_operation decorator on the process_func,
    not by this rate limiter. This keeps retry policy and error classification at the
    individual connector level while maintaining rate limiting here.
    """

    rate_per_second: float  # Changed to float to support settings like 4.5
    connector_name: str
    max_concurrent_tasks: int

    def __attrs_post_init__(self):
        """Initialize runtime state after attrs construction."""
        self.rate_delay = 1.0 / self.rate_per_second  # e.g. 0.2s for 5/sec
        self.work_queue: asyncio.Queue[WorkItem] = asyncio.Queue()
        self.running_tasks: set[asyncio.Task] = set()
        self.completed_results: dict[str, Any] = {}
        self.batch_start_time: float = 0.0
        self.total_expected_items: int = 0
        self.shutdown_event = asyncio.Event()

        # Contextual logger
        self.logger = logger.bind(
            connector=self.connector_name,
            rate_per_second=self.rate_per_second,
            max_concurrent=self.max_concurrent_tasks,
        )

        self.logger.info(
            f"Initialized {self.connector_name} rate-limited batch processor",
            rate_delay_ms=round(self.rate_delay * 1000, 1),
        )

    async def process_batch(
        self,
        items: list[TItem],
        process_func: Callable[[TItem], Any],
    ) -> AsyncIterator[tuple[str, Any]]:
        """Process batch of items with rate limiting and concurrent execution.

        Args:
            items: List of items to process
            process_func: Async function to process individual items

        Yields:
            Tuples of (item_id, result) as items complete successfully
        """
        if not items:
            self.logger.warning("Empty batch provided for processing")
            return

        self.batch_start_time = time.time()
        self.total_expected_items = len(items)

        self.logger.info(
            f"Starting batch processing for {len(items)} items",
            batch_size=len(items),
            expected_duration_seconds=round(len(items) * self.rate_delay, 1),
            batch_start_time=self.batch_start_time,
        )

        # Queue all initial work items
        for item in items:
            work_item = WorkItem(
                item_id=str(uuid.uuid4()),
                item=item,
            )
            await self.work_queue.put(work_item)

            self.logger.debug(
                "Queued work item for processing",
                item_id=work_item.item_id,
                queue_size=self.work_queue.qsize(),
                milliseconds_since_batch_start=round(
                    (time.time() - self.batch_start_time) * 1000, 1
                ),
            )

        # Start background processors
        rate_limiter_task = asyncio.create_task(self._rate_limiter_loop(process_func))

        try:
            # Yield results as they become available
            completed_count = 0
            async for item_id, result in self._collect_results():
                completed_count += 1

                self.logger.info(
                    f"Batch item completed ({completed_count}/{self.total_expected_items})",
                    item_id=item_id,
                    completed_count=completed_count,
                    total_expected=self.total_expected_items,
                    progress_percent=round(
                        (completed_count / self.total_expected_items) * 100, 1
                    ),
                    milliseconds_since_batch_start=round(
                        (time.time() - self.batch_start_time) * 1000, 1
                    ),
                )

                yield item_id, result

                # Check if batch is complete
                if completed_count >= self.total_expected_items:
                    break

        finally:
            # Clean shutdown
            self.shutdown_event.set()

            batch_duration = time.time() - self.batch_start_time
            self.logger.info(
                f"Batch processing completed for {self.connector_name}",
                total_items=self.total_expected_items,
                batch_duration_seconds=round(batch_duration, 2),
                average_items_per_second=round(
                    self.total_expected_items / batch_duration, 2
                ),
                final_queue_size=self.work_queue.qsize(),
                running_tasks_count=len(self.running_tasks),
            )

            # Cancel background tasks
            if not rate_limiter_task.done():
                rate_limiter_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await rate_limiter_task

    async def _rate_limiter_loop(self, process_func: Callable[[Any], Any]) -> None:
        """Background loop that launches requests at controlled intervals.

        This loop maintains a steady launch rate (e.g. every 200ms for 5/sec) regardless
        of how long individual tasks take to complete. Tasks run concurrently after launch.
        """
        self.logger.info(
            f"Starting rate limiter loop for {self.connector_name}",
            launch_interval_ms=round(self.rate_delay * 1000, 1),
        )

        launch_count = 0
        next_launch_time = time.time()  # Start immediately

        while not self.shutdown_event.is_set():
            try:
                # Wait until it's time for the next launch
                now = time.time()
                if now < next_launch_time:
                    sleep_time = next_launch_time - now
                    await asyncio.sleep(sleep_time)

                # Try to get work item (non-blocking check)
                try:
                    work_item = self.work_queue.get_nowait()
                except asyncio.QueueEmpty:
                    # No work available, schedule next check and continue
                    next_launch_time += self.rate_delay
                    continue

                # Respect concurrent task limit
                if len(self.running_tasks) >= self.max_concurrent_tasks:
                    self.logger.warning(
                        "Rate limiter waiting for task slot",
                        running_tasks=len(self.running_tasks),
                        max_concurrent=self.max_concurrent_tasks,
                    )
                    # Re-queue the work item and try again next cycle
                    await self.work_queue.put(work_item)
                    next_launch_time += self.rate_delay
                    continue

                # Launch the task immediately
                launch_count += 1
                launch_time = time.time()

                task = asyncio.create_task(
                    self._execute_work_item(work_item, process_func),
                    name=f"{self.connector_name}_task_{work_item.item_id[:8]}",
                )
                self.running_tasks.add(task)

                # Clean up completed tasks
                def remove_completed_task(completed_task: asyncio.Task) -> None:
                    """Remove task from running_tasks set when completed."""
                    self.running_tasks.discard(completed_task)

                task.add_done_callback(remove_completed_task)

                self.logger.info(
                    f"Launched request {launch_count} for {self.connector_name}",
                    item_id=work_item.item_id,
                    launch_count=launch_count,
                    launch_time=launch_time,
                    running_tasks=len(self.running_tasks),
                    queue_size=self.work_queue.qsize(),
                    milliseconds_since_batch_start=round(
                        (launch_time - self.batch_start_time) * 1000, 1
                    ),
                    actual_interval_ms=round(
                        (launch_time - (next_launch_time - self.rate_delay)) * 1000, 1
                    )
                    if launch_count > 1
                    else 0,
                    expected_launch_interval_ms=round(self.rate_delay * 1000, 1),
                )

                # Schedule next launch (maintain steady rate regardless of task completion time)
                next_launch_time += self.rate_delay

            except Exception as e:
                self.logger.error(
                    f"Rate limiter loop error for {self.connector_name}",
                    error=str(e),
                    error_type=type(e).__name__,
                )
                # Schedule next launch even on error
                next_launch_time = time.time() + self.rate_delay

        self.logger.info(
            f"Rate limiter loop shutdown for {self.connector_name}",
            total_launches=launch_count,
            final_running_tasks=len(self.running_tasks),
        )

    async def _execute_work_item(
        self, work_item: WorkItem, process_func: Callable[[Any], Any]
    ) -> None:
        """Execute individual work item and handle result/retry logic."""
        execution_start = time.time()

        self.logger.debug(
            f"Executing work item for {self.connector_name}",
            item_id=work_item.item_id,
            execution_start=execution_start,
            milliseconds_since_queued=round(
                (execution_start - work_item.queued_at) * 1000, 1
            ),
        )

        try:
            # Execute the actual work
            result = await process_func(work_item.item)
            execution_duration = time.time() - execution_start

            # Store successful result
            self.completed_results[work_item.item_id] = result

            self.logger.info(
                f"Work item completed successfully for {self.connector_name}",
                item_id=work_item.item_id,
                execution_duration_ms=round(execution_duration * 1000, 1),
                result_available=result is not None,
                milliseconds_since_batch_start=round(
                    (time.time() - self.batch_start_time) * 1000, 1
                ),
            )

        except Exception as e:
            execution_duration = time.time() - execution_start

            self.logger.error(
                f"Work item failed for {self.connector_name}",
                item_id=work_item.item_id,
                error=str(e),
                error_type=type(e).__name__,
                execution_duration_ms=round(execution_duration * 1000, 1),
                milliseconds_since_batch_start=round(
                    (time.time() - self.batch_start_time) * 1000, 1
                ),
            )

            # Handle retry logic - let the calling @resilient_operation decorator handle retries
            # We just log the failure and mark as complete with None result
            self.completed_results[work_item.item_id] = None

            self.logger.warning(
                f"Work item failed for {self.connector_name}",
                item_id=work_item.item_id,
                error_details="Retry handled by @resilient_operation decorator",
            )

    async def _collect_results(self) -> AsyncIterator[tuple[str, Any]]:
        """Collect and yield results as they become available."""
        yielded_items = set()

        while len(yielded_items) < self.total_expected_items:
            await asyncio.sleep(
                _RESULT_POLLING_INTERVAL_MS / 1000.0
            )  # Convert ms to seconds

            # Check for newly completed results
            for item_id, result in self.completed_results.items():
                if item_id not in yielded_items:
                    yielded_items.add(item_id)
                    yield item_id, result
