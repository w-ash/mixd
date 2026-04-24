"""Rich progress bar provider for CLI progress display.

Implements ProgressSubscriber protocol using Rich library to display beautiful
progress bars, spinners, and status information for long-running operations.
Uses Rich Live Display with Progress for proper stdout/stderr coordination.
"""

import asyncio
import contextlib
from typing import TYPE_CHECKING, Self, TypedDict, cast, override

from attrs import define
from rich.live import Live
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    ProgressColumn,
    SpinnerColumn,
    Task,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
)

from src.config import get_logger

if TYPE_CHECKING:
    from structlog.stdlib import BoundLogger

from src.domain.entities.progress import (
    OperationStatus,
    ProgressEvent,
    ProgressOperation,
)
from src.interface.cli.console import GOLD

logger = get_logger(__name__).bind(service="rich_progress_provider")


class _ProgressUpdateKwargs(TypedDict, total=False):
    """Typed kwargs for Rich Progress.update()."""

    completed: int
    description: str
    total: int | None


class ETAColumn(ProgressColumn):
    """Custom progress column that shows ETA from event metadata."""

    @override
    def render(self, task: Task) -> str:
        """Render ETA from task description or metadata."""
        if "eta_seconds" in task.fields:
            eta_seconds = cast("float | None", task.fields["eta_seconds"])
            if eta_seconds is not None and eta_seconds > 0:
                minutes, seconds = divmod(int(eta_seconds), 60)
                if minutes > 0:
                    return f"{minutes}m {seconds}s remaining"
                return f"{seconds}s remaining"
        return ""


class RateColumn(ProgressColumn):
    """Custom progress column that shows processing rate from event metadata."""

    @override
    def render(self, task: Task) -> str:
        """Render processing rate from task metadata."""
        if "items_per_second" in task.fields:
            rate = cast("float | None", task.fields["items_per_second"])
            if rate is not None and rate > 0:
                return f"{rate:.1f}/sec"
        return ""


@define(slots=True)
class OperationTask:
    """Tracks Rich progress task for a single operation."""

    operation_id: str
    task_id: TaskID
    operation: ProgressOperation
    is_active: bool = True


class RichProgressProvider:
    """CLI progress provider using Rich Progress.console for unified terminal output.

    Implements ProgressSubscriber protocol using Rich Progress as the single source
    of truth for all console output. Progress manages its own Live Display internally,
    and all logging is routed through Progress.console for proper coordination.

    Features:
    - Progress bars with spinner, percentage, ETA, and rate columns
    - All logs appear above pinned progress bars
    - Single console coordination prevents competing output systems
    - Automatic cleanup of completed operations
    """

    _show_rate: bool
    _progress: Progress
    _live: Live
    _progress_started: bool
    _lock: asyncio.Lock
    _logger: BoundLogger

    def __init__(self, show_rate: bool = True):
        """Initialize Rich progress provider with Progress.console coordination.

        Args:
            show_rate: Whether to show processing rate column
        """
        self._show_rate = show_rate

        # Create Rich progress display with custom columns
        progress_columns: list[ProgressColumn | str] = [
            SpinnerColumn(),
            TextColumn(f"[bold {GOLD}]{{task.description}}", justify="left"),
            BarColumn(bar_width=None),
            MofNCompleteColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.1f}%"),
            "•",
            TimeElapsedColumn(),
        ]

        if show_rate:
            progress_columns.extend([RateColumn(), "•"])

        progress_columns.append(ETAColumn())

        # Create Progress instance
        self._progress = Progress(
            *progress_columns,
            refresh_per_second=10,
        )

        # Create Live Display wrapper for stdout/stderr capture and pinning
        self._live = Live(
            self._progress,
            refresh_per_second=10,
            redirect_stdout=True,  # Capture any remaining stdout
            redirect_stderr=True,  # Capture any remaining stderr
            transient=False,  # Keep progress visible after completion
        )

        self._operation_tasks: dict[str, OperationTask] = {}
        self._cleanup_tasks: set[asyncio.Task[None]] = (
            set()
        )  # Track cleanup tasks for proper cancellation
        self._progress_started = False
        self._lock = asyncio.Lock()

        # Contextual logger
        self._logger = logger.bind(provider_type="rich_progress", show_rate=show_rate)

        self._logger.info(
            "RichProgressProvider initialized with Progress.console coordination"
        )

    async def start_display(self) -> None:
        """Start Live Display with Progress and unified logging coordination."""
        if not self._progress_started:
            # Start Live Display (which includes Progress)
            self._live.start()

            # Configure unified logging through Live Display's console
            self._configure_unified_logging()

            self._progress_started = True
            self._logger.debug("Live Display started with unified console logging")

    async def stop_display(self) -> None:
        """Stop Live Display and restore normal logging."""
        async with self._lock:
            if self._progress_started:
                # Cancel all pending cleanup tasks
                cleanup_count = len(self._cleanup_tasks)
                tasks_snapshot = list(self._cleanup_tasks)
                for task in tasks_snapshot:
                    if not task.done():
                        _ = task.cancel()

                # Wait for all cleanup tasks to finish cancellation
                for task in tasks_snapshot:
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await task

                self._cleanup_tasks.clear()

                # Restore normal logging first
                self._restore_normal_logging()

                # Stop Live Display (which includes Progress)
                self._live.stop()

                self._progress_started = False

                # Clear all operation tasks
                operation_count = len(self._operation_tasks)
                self._operation_tasks.clear()

                self._logger.info(
                    "Live Display stopped and logging restored",
                    cleared_operations=operation_count,
                    cancelled_cleanup_tasks=cleanup_count,
                )

    async def on_operation_started(self, operation: ProgressOperation) -> None:
        """Handle notification that a new operation has started.

        Creates a new Rich progress task for the operation. Sub-operations
        (those with parent_operation_id in metadata) are displayed with an
        indented prefix and shorter cleanup delay.

        Args:
            operation: Newly started operation
        """
        async with self._lock:
            # Ensure progress display is started
            if not self._progress_started:
                await self.start_display()

            # Sub-operations get indented description
            description = operation.description
            if operation.metadata.get("parent_operation_id"):
                description = f"  ↳ {description}"

            # Create Rich task for this operation
            task_id = self._progress.add_task(
                description=description,
                total=operation.total_items,
                completed=0,
            )

            # Track operation task
            operation_task = OperationTask(
                operation_id=operation.operation_id,
                task_id=task_id,
                operation=operation,
            )
            self._operation_tasks[operation.operation_id] = operation_task

            self._logger.info(
                "Operation progress task created",
                operation_id=operation.operation_id,
                description=operation.description,
                total_items=operation.total_items,
                is_determinate=operation.is_determinate,
                task_id=str(task_id),
            )

    async def on_progress_event(self, event: ProgressEvent) -> None:
        """Handle a progress event from an active operation.

        Updates the corresponding Rich progress task with current progress.

        Args:
            event: Progress event with current status
        """
        async with self._lock:
            operation_task = self._operation_tasks.get(event.operation_id)
            if operation_task is None or not operation_task.is_active:
                self._logger.warning(
                    "Progress event for unknown or inactive operation",
                    operation_id=event.operation_id,
                )
                return

            # Update Rich task with current progress
            task_update_kwargs: _ProgressUpdateKwargs = {
                "completed": event.current,
                "description": event.message,
            }

            # Update total if it changed (for operations that discover total during execution)
            if event.total != operation_task.operation.total_items:
                task_update_kwargs["total"] = event.total
                # Update our tracked operation
                operation_task.operation = operation_task.operation.with_metadata(
                    total_items=event.total
                )

            # Add metadata to task fields for custom columns
            task_fields: dict[str, float] = {}
            eta = event.metadata.get("eta_seconds")
            if isinstance(eta, (int, float)):
                task_fields["eta_seconds"] = float(eta)
            rate = event.metadata.get("items_per_second")
            if isinstance(rate, (int, float)):
                task_fields["items_per_second"] = float(rate)

            # Update the task
            self._progress.update(operation_task.task_id, **task_update_kwargs)

            # Update task fields if we have custom metadata
            if task_fields:
                task = self._progress.tasks[operation_task.task_id]
                if not hasattr(task, "fields"):
                    task.fields = {}
                task.fields.update(task_fields)

    async def on_operation_completed(
        self, operation_id: str, final_status: OperationStatus
    ) -> None:
        """Handle notification that an operation has finished.

        Updates the progress task with final status and schedules cleanup.

        Args:
            operation_id: ID of completed operation
            final_status: How the operation ended (success/failure/cancellation)
        """
        async with self._lock:
            operation_task = self._operation_tasks.get(operation_id)
            if operation_task is None:
                self._logger.warning(
                    "Completion notification for unknown operation",
                    operation_id=operation_id,
                )
                return

            # Update task description to show final status
            if final_status == OperationStatus.COMPLETED:
                final_description = (
                    f"✅ {operation_task.operation.description} - Completed"
                )
                # Set progress to 100% for completed operations
                if operation_task.operation.is_determinate:
                    self._progress.update(
                        operation_task.task_id,
                        completed=operation_task.operation.total_items,
                        description=final_description,
                    )
                else:
                    self._progress.update(
                        operation_task.task_id, description=final_description
                    )
            elif final_status == OperationStatus.FAILED:
                final_description = (
                    f"❌ {operation_task.operation.description} - Failed"
                )
                self._progress.update(
                    operation_task.task_id, description=final_description
                )
            elif final_status == OperationStatus.CANCELLED:
                final_description = (
                    f"⚠️  {operation_task.operation.description} - Cancelled"
                )
                self._progress.update(
                    operation_task.task_id, description=final_description
                )

            # Mark as inactive but keep visible briefly
            operation_task.is_active = False

            self._logger.info(
                "Operation progress completed",
                operation_id=operation_id,
                final_status=final_status.value,
                description=operation_task.operation.description,
            )

            # Sub-operations clean up faster (0.5s vs 2.0s)
            is_sub_operation = bool(
                operation_task.operation.metadata.get("parent_operation_id")
            )
            cleanup_delay = 0.5 if is_sub_operation else 2.0

            # Schedule cleanup after brief display of final status
            cleanup_task = asyncio.create_task(
                self._cleanup_completed_task(operation_id, delay_seconds=cleanup_delay)
            )
            # Track cleanup task for proper cancellation
            self._cleanup_tasks.add(cleanup_task)
            # Remove from tracking when done
            cleanup_task.add_done_callback(self._cleanup_tasks.discard)

    async def _cleanup_completed_task(
        self, operation_id: str, delay_seconds: float = 2.0
    ) -> None:
        """Clean up a completed progress task after a delay.

        Args:
            operation_id: ID of operation to clean up
            delay_seconds: How long to wait before cleanup

        Note:
            This task may be cancelled during shutdown. CancelledError
            is allowed to propagate per Python 3.14 best practices.
        """
        try:
            await asyncio.sleep(delay_seconds)

            async with self._lock:
                operation_task = self._operation_tasks.get(operation_id)
                if operation_task is not None:
                    # Remove from Rich progress display
                    with contextlib.suppress(KeyError):
                        # Task may have already been removed
                        self._progress.remove_task(operation_task.task_id)

                    # Remove from our tracking
                    del self._operation_tasks[operation_id]

                    self._logger.debug(
                        "Completed progress task cleaned up", operation_id=operation_id
                    )
        except asyncio.CancelledError:
            # Task was cancelled during shutdown - this is expected
            # Re-raise to allow proper cancellation propagation (Python 3.14 best practice)
            self._logger.debug(
                "Cleanup task cancelled during shutdown", operation_id=operation_id
            )
            raise

    def get_console(self):
        """Get the Live Display console for coordinated output.

        Returns:
            Rich Console instance from Live Display for unified output
        """
        return self._live.console

    @property
    def is_display_active(self) -> bool:
        """Check if progress display is currently active."""
        return self._progress_started

    @property
    def active_operation_count(self) -> int:
        """Get number of currently tracked operations."""
        return len([task for task in self._operation_tasks.values() if task.is_active])

    async def __aenter__(self) -> Self:
        """Async context manager entry - starts Live Display."""
        await self.start_display()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        _exc_val: BaseException | None,
        _exc_tb: object,
    ) -> None:
        """Async context manager exit - stops Progress display."""
        await self.stop_display()

    def _configure_unified_logging(self) -> None:
        """Configure unified logging through Live Display console using centralized config."""
        try:
            from src.config.logging import enable_unified_console_output

            enable_unified_console_output(self._live.console)
            self._logger.debug(
                "Unified logging configured through Live Display console"
            )
        except Exception as e:
            # Fallback error reporting with rich markup
            self._live.console.print(
                f"[bold yellow]Warning:[/bold yellow] Failed to configure unified logging: {e}"
            )

    def _restore_normal_logging(self) -> None:
        """Restore normal logging configuration."""
        try:
            from src.config.logging import restore_standard_console_output

            restore_standard_console_output()
            self._logger.debug("Normal logging configuration restored")
        except Exception as e:
            # Use regular print as fallback since Progress is stopping
            print(f"Warning: Failed to restore normal logging: {e}")
