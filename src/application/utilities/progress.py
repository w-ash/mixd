"""Progress tracking for long-running operations.

Displays progress bars and spinners during data imports, API calls, and bulk operations.
Supports both determinate progress (X of Y items) and indeterminate spinners.
Allows swapping display implementations for CLI vs web UI without changing business logic.
"""

from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from attrs import define, field


@define(frozen=True, slots=True)
class ProgressOperation:
    """Represents a single trackable operation with progress state.

    Tracks operations like "Importing 500 tracks" or "Syncing playlists"
    with current/total counts, timestamps, and metadata.
    """

    operation_id: str = field(factory=lambda: str(uuid4()))
    description: str = "Processing..."
    total_items: int | None = None  # None = indeterminate/spinner mode
    start_time: datetime = field(factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = field(factory=dict)


class ProgressProvider(Protocol):
    """Interface for displaying progress to users.

    Implementations handle CLI progress bars, web UI updates, notifications, etc.
    Allows business logic to track progress without coupling to specific display code.
    """

    def start_operation(self, operation: ProgressOperation) -> str:
        """Begin displaying progress for an operation.

        Args:
            operation: Operation details and initial state.

        Returns:
            Unique ID for subsequent progress updates.
        """
        ...

    def update_progress(
        self,
        operation_id: str,
        current: int,
        total: int | None = None,
        description: str | None = None,
    ) -> None:
        """Update progress display with new current/total values.

        Args:
            operation_id: ID from start_operation().
            current: Items completed so far.
            total: Total items (updates indeterminate to determinate).
            description: New operation description text.
        """
        ...

    def set_description(self, operation_id: str, description: str) -> None:
        """Change displayed operation description.

        Args:
            operation_id: Operation to update.
            description: New description text.
        """
        ...

    def complete_operation(self, operation_id: str) -> None:
        """Finish and hide progress display.

        Args:
            operation_id: Operation to complete.
        """
        ...


class NoOpProgressProvider:
    """Silent progress provider for headless scripts and tests."""

    def start_operation(self, operation: ProgressOperation) -> str:
        return operation.operation_id

    def update_progress(
        self,
        operation_id: str,
        current: int,
        total: int | None = None,
        description: str | None = None,
    ) -> None:
        pass

    def set_description(self, operation_id: str, description: str) -> None:
        pass

    def complete_operation(self, operation_id: str) -> None:
        pass


# Global provider instance - can be swapped for different environments
_global_provider: ProgressProvider | None = None


def set_progress_provider(provider: ProgressProvider) -> None:
    """Configure global progress display implementation.

    Args:
        provider: Display implementation (CLI, web UI, etc).
    """
    global _global_provider
    _global_provider = provider


def get_progress_provider() -> ProgressProvider:
    """Get currently configured progress display.

    Returns:
        Active provider or silent no-op if none configured.
    """
    return _global_provider or NoOpProgressProvider()


def create_operation(
    description: str,
    total_items: int | None = None,
    **metadata: Any,
) -> ProgressOperation:
    """Create a trackable operation instance.

    Args:
        description: User-visible operation name like "Importing tracks".
        total_items: Expected item count (None for indeterminate spinner).
        **metadata: Additional data for logging/debugging.

    Returns:
        Operation ready for progress tracking.
    """
    return ProgressOperation(
        description=description,
        total_items=total_items,
        metadata=metadata,
    )
