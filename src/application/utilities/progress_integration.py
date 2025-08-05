"""Progress tracking utilities for music data operations.

Decorators and context managers that add visual progress bars to long-running
operations like importing playlists, matching tracks, and syncing with APIs.
Handles both single operations and batch processing with automatic completion.
"""

import asyncio
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any, ParamSpec, Protocol, TypeVar

from src.domain.entities.operations import OperationResult

from .progress import (
    ProgressProvider,
    create_operation,
    get_progress_provider,
    set_progress_provider,
)

T = TypeVar("T")
P = ParamSpec("P")
U = TypeVar("U")


# Protocols for dependency injection
class Console(Protocol):
    """Terminal output interface for progress messages."""

    def print(self, text: str) -> None:
        """Print text to terminal."""
        ...


class UIProvider(Protocol):
    """User interface for displaying operation results."""

    def display_operation_result(
        self,
        result: OperationResult,
        title: str | None = None,
        next_step_message: str | None = None,
    ) -> None:
        """Show operation summary with success/failure stats."""
        ...


def with_progress(
    description: str,
    *,
    estimate_total: Callable[[Any], int] | None = None,
    extract_items: Callable[[Any], list[Any]] | None = None,
    success_text: str = "Operation completed!",
    console: Console | None = None,
    progress_provider_factory: Callable[[], ProgressProvider] | None = None,
) -> Callable[[Callable[P, Awaitable[U]]], Callable[P, Awaitable[U]]]:
    """Add progress bar to any async function.

    Wraps functions with automatic progress tracking. Shows spinning indicator
    or percentage completion if total items can be estimated. Displays success
    message when complete.

    Args:
        description: What operation is being performed (e.g. "Importing tracks")
        estimate_total: Function to guess total items from first argument
        extract_items: Function to extract item list from first argument
        success_text: Message shown on successful completion
        console: Where to print success messages
        progress_provider_factory: Custom progress display factory

    Returns:
        Decorated function with automatic progress tracking

    Examples:
        @with_progress("Matching tracks to LastFM")
        async def match_tracks(tracks: list[Track]) -> MatchResults:
            # Progress bar shows while function runs

        @with_progress("Processing playlist",
                      estimate_total=lambda playlist: len(playlist.tracks))
        async def process_playlist(playlist: Playlist) -> Result:
            # Shows percentage based on track count
    """

    def decorator(func: Callable[P, Awaitable[U]]) -> Callable[P, Awaitable[U]]:
        @wraps(func)
        async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> U:
            # Smart total estimation
            import contextlib

            total_items = None
            if estimate_total:
                with contextlib.suppress(Exception):
                    total_items = estimate_total(args[0] if args else None)
            elif extract_items:
                with contextlib.suppress(Exception):
                    items = extract_items(args[0] if args else None)
                    total_items = len(items) if items else None

            # Create and start operation
            operation = create_operation(description, total_items)

            # Use injected progress provider or get global one
            if progress_provider_factory:
                provider = progress_provider_factory()
                set_progress_provider(provider)
            else:
                provider = get_progress_provider()

            operation_id = provider.start_operation(operation)

            try:
                # Execute with progress context
                result = await func(*args, **kwargs)

                # Mark as complete
                provider.complete_operation(operation_id)

                # Show success message if console available
                if console:
                    console.print(f"[green]✓ {success_text}[/green]")

                return result

            except Exception:
                # Clean up on failure
                provider.complete_operation(operation_id)
                raise

        @wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            # Handle sync calls by wrapping in asyncio.run
            return asyncio.run(async_wrapper(*args, **kwargs))

        # Return appropriate wrapper based on function signature
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper

    return decorator


class DatabaseProgressContext:
    """Progress tracking for database operations that prevents SQLite locks.

    Context manager that shows progress while avoiding database lock errors.
    Creates fresh database sessions for each operation instead of holding
    connections open during long-running tasks. Shows results summary when done.

    Usage:
        async with DatabaseProgressContext(
            description="Importing tracks...",
            display_title="Import Results"
        ) as progress:
            # Each database call gets its own session
            # Progress updates shown to user
            # Results displayed when complete
    """

    def __init__(
        self,
        description: str,
        success_text: str = "Operation completed!",
        display_title: str | None = None,
        next_step_message: str | None = None,
        console: Console | None = None,
        ui_provider: UIProvider | None = None,
    ):
        """Initialize database progress context.

        Args:
            description: What operation is being performed
            success_text: Message to show when operation succeeds
            display_title: Title for results summary display
            next_step_message: Hint about what user should do next
            console: Where to print success messages
            ui_provider: Where to display detailed results
        """
        self.description = description
        self.success_text = success_text
        self.display_title = display_title
        self.next_step_message = next_step_message
        self.console = console
        self.ui_provider = ui_provider

        self._operation_id: str | None = None
        self._provider: ProgressProvider | None = None
        self._result: OperationResult | None = None

    async def __aenter__(self) -> "DatabaseProgressContext":
        """Start showing progress and return context for database operations."""
        # Create and start progress operation
        operation = create_operation(self.description)
        self._provider = get_progress_provider()
        self._operation_id = self._provider.start_operation(operation)

        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        """Stop progress tracking and show results if operation succeeded."""
        # Complete progress operation
        if self._provider and self._operation_id:
            self._provider.complete_operation(self._operation_id)

        # Display success message and results only if no exception
        if exc_type is None:
            if self.console:
                self.console.print(f"[green]✓ {self.success_text}[/green]")

            if self.ui_provider and self._result:
                self.ui_provider.display_operation_result(
                    result=self._result,
                    title=self.display_title,
                    next_step_message=self.next_step_message,
                )

    def set_result(self, result: OperationResult) -> None:
        """Store operation results for display when context exits."""
        self._result = result


def batch_progress_wrapper(
    items: list[Any],
    process_func: Callable,
    *,
    operation_description: str = "Processing items",
    batch_size: int = 50,
    progress_provider: ProgressProvider | None = None,
) -> Callable[[], Awaitable[dict[int, Any]]]:
    """Create function that processes large lists in chunks with progress updates.

    Splits large item lists into smaller batches to avoid memory issues and
    provide regular progress updates. Shows current batch number and overall
    completion percentage.

    Args:
        items: List of items to process in batches
        process_func: Function that processes each batch
        operation_description: Description shown in progress bar
        batch_size: Number of items per batch
        progress_provider: Custom progress display

    Returns:
        Async function that processes all items with progress tracking
    """

    async def process_with_progress() -> dict[int, Any]:
        if not items:
            return {}

        # Create progress operation
        operation = create_operation(operation_description, len(items))
        provider = progress_provider or get_progress_provider()
        operation_id = provider.start_operation(operation)

        try:
            results = {}
            processed_items = 0

            # Process in batches
            for i in range(0, len(items), batch_size):
                batch = items[i : i + batch_size]
                batch_num = (i // batch_size) + 1
                total_batches = (len(items) + batch_size - 1) // batch_size

                # Update progress description
                description = (
                    f"{operation_description} (batch {batch_num}/{total_batches})"
                )
                provider.set_description(operation_id, description)

                # Process batch
                batch_results = await process_func(batch)
                if batch_results:
                    results.update(batch_results)

                processed_items += len(batch)
                provider.update_progress(operation_id, processed_items)

            provider.complete_operation(operation_id)
            return results

        except Exception:
            provider.complete_operation(operation_id)
            raise

    return process_with_progress
