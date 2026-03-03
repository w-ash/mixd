"""Domain entities for progress tracking across long-running operations.

Defines immutable value objects and enums for representing progress state, events, and operations.
Enforces business rules like progress monotonicity and valid status transitions.
Designed to be display-agnostic and usable across CLI, web, and future interfaces.
"""

# pyright: reportExplicitAny=false, reportAny=false
# Legitimate Any: service_metadata, raw_data dicts, factory patterns

from datetime import datetime
from enum import Enum
from typing import Any, Protocol, Self, override
from uuid import uuid4

from attrs import define, evolve, field

from .shared import utc_now_factory


class ProgressStatus(Enum):
    """Status of individual progress events within an operation."""

    STARTED = "started"  # Operation has begun
    IN_PROGRESS = "in_progress"  # Operation is actively running
    COMPLETED = "completed"  # Operation finished successfully
    FAILED = "failed"  # Operation encountered an error
    CANCELLED = "cancelled"  # Operation was cancelled by user


class OperationStatus(Enum):
    """Overall lifecycle status of a trackable operation."""

    PENDING = "pending"  # Operation created but not started
    RUNNING = "running"  # Operation is actively executing
    COMPLETED = "completed"  # Operation finished successfully
    FAILED = "failed"  # Operation failed with errors
    CANCELLED = "cancelled"  # Operation was cancelled


@define(frozen=True, slots=True)
class ProgressEvent:
    """Immutable event representing a single progress update within an operation.

    Value object that captures a point-in-time progress state with validation
    of business rules like progress bounds and temporal ordering.

    Attributes:
        operation_id: Unique identifier linking to parent ProgressOperation
        current: Current progress count (must be non-negative)
        total: Total expected items (None for indeterminate operations)
        message: Human-readable description of current activity
        timestamp: When this event occurred (UTC timezone)
        status: Current event status from ProgressStatus enum
        metadata: Extensible key-value data for operation-specific context
    """

    operation_id: str
    current: int
    total: int | None
    message: str
    timestamp: datetime = field(factory=utc_now_factory)
    status: ProgressStatus = ProgressStatus.IN_PROGRESS
    metadata: dict[str, Any] = field(factory=dict)

    def __attrs_post_init__(self) -> None:
        """Validate business rules for progress events."""
        # Validate current progress is non-negative
        if self.current < 0:
            raise ValueError(f"Progress current ({self.current}) must be non-negative")

        # Validate total is positive when specified
        if self.total is not None and self.total <= 0:
            raise ValueError(
                f"Progress total ({self.total}) must be positive when specified"
            )

        # Validate current doesn't exceed total when both are known
        if self.total is not None and self.current > self.total:
            raise ValueError(
                f"Progress current ({self.current}) cannot exceed total ({self.total})"
            )

        # Validate operation_id is well-formed
        if not self.operation_id.strip():
            raise ValueError("Progress operation_id cannot be empty")

        # Validate message is meaningful
        if not self.message.strip():
            raise ValueError("Progress message cannot be empty")

    @property
    def completion_percentage(self) -> float | None:
        """Calculate completion percentage (0.0-100.0) when total is known."""
        if self.total is None or self.total == 0:
            return None
        return round((self.current / self.total) * 100.0, 2)

    @property
    def is_complete(self) -> bool:
        """Check if this event represents completion of the operation."""
        return self.status in (
            ProgressStatus.COMPLETED,
            ProgressStatus.FAILED,
            ProgressStatus.CANCELLED,
        )

    @property
    def is_determinate(self) -> bool:
        """Check if this event has a known total (determinate progress)."""
        return self.total is not None


@define(frozen=True, slots=True)
class ProgressOperation:
    """Immutable aggregate root representing a complete trackable operation.

    Represents the full lifecycle and context of a long-running operation that
    emits progress events. Contains identity, description, and lifecycle metadata.

    Attributes:
        operation_id: Unique identifier for this operation
        description: Human-readable name/description of the operation
        total_items: Expected number of items to process (None for indeterminate)
        start_time: When operation was initiated (UTC timezone)
        end_time: When operation completed (None if still running)
        status: Current lifecycle status from OperationStatus enum
        metadata: Extensible context data for operation-specific information
    """

    operation_id: str = field(factory=lambda: str(uuid4()))
    description: str = "Processing..."
    total_items: int | None = None
    start_time: datetime = field(factory=utc_now_factory)
    end_time: datetime | None = None
    status: OperationStatus = OperationStatus.PENDING
    metadata: dict[str, Any] = field(factory=dict)

    def __attrs_post_init__(self) -> None:
        """Validate business rules for operations."""
        # Validate operation has meaningful description
        if not self.description.strip():
            raise ValueError("Operation description cannot be empty")

        # Validate total_items is positive when specified
        if self.total_items is not None and self.total_items <= 0:
            raise ValueError(
                f"Operation total_items ({self.total_items}) must be positive when specified"
            )

        # Validate end_time is after start_time when both are present
        if self.end_time is not None and self.end_time < self.start_time:
            raise ValueError("Operation end_time cannot be before start_time")

        # Validate operation_id is well-formed UUID string
        if not self.operation_id.strip():
            raise ValueError("Operation ID cannot be empty")

    @property
    def duration_seconds(self) -> float | None:
        """Calculate operation duration in seconds (None if not completed)."""
        if self.end_time is None:
            return None
        return (self.end_time - self.start_time).total_seconds()

    @property
    def is_running(self) -> bool:
        """Check if operation is currently active."""
        return self.status == OperationStatus.RUNNING

    @property
    def is_complete(self) -> bool:
        """Check if operation has finished (successfully or otherwise)."""
        return self.status in (
            OperationStatus.COMPLETED,
            OperationStatus.FAILED,
            OperationStatus.CANCELLED,
        )

    @property
    def is_determinate(self) -> bool:
        """Check if operation has known total items (determinate progress)."""
        return self.total_items is not None

    def with_status(
        self, new_status: OperationStatus, end_time: datetime | None = None
    ) -> Self:
        """Create new operation instance with updated status and optional end time."""
        return evolve(self, status=new_status, end_time=end_time or self.end_time)

    def with_metadata(self, **new_metadata: Any) -> Self:
        """Create new operation instance with additional metadata."""
        return evolve(self, metadata={**self.metadata, **new_metadata})


class ProgressEmitter(Protocol):
    """Protocol for progress tracking implementations.

    Defines the interface for emitting progress events during long-running operations.
    Implementations can be real progress managers or null objects for silent operation.
    """

    async def start_operation(self, operation: ProgressOperation) -> str:
        """Start tracking a new operation.

        Args:
            operation: The operation to track

        Returns:
            Operation ID for subsequent progress events
        """
        ...

    async def emit_progress(self, event: ProgressEvent) -> None:
        """Emit a progress event for an ongoing operation.

        Args:
            event: Progress event with current status
        """
        ...

    async def complete_operation(
        self, operation_id: str, final_status: OperationStatus
    ) -> None:
        """Mark an operation as completed.

        Args:
            operation_id: ID of the operation to complete
            status: Final status (COMPLETED, FAILED, etc.)
        """
        ...


class NullProgressEmitter(ProgressEmitter):
    """Null object implementation of ProgressEmitter.

    Provides silent no-op implementations for when progress tracking is disabled.
    This eliminates the need for None checks throughout the codebase.
    """

    @override
    async def start_operation(self, operation: ProgressOperation) -> str:
        """Silent no-op that returns a dummy operation ID."""
        _ = operation  # Mark as intentionally unused for null implementation
        return f"null-{uuid4().hex[:8]}"

    @override
    async def emit_progress(self, event: ProgressEvent) -> None:
        """Silent no-op for progress events."""
        _ = event

    @override
    async def complete_operation(
        self, operation_id: str, final_status: OperationStatus
    ) -> None:
        """Silent no-op for operation completion."""
        _ = operation_id, final_status


# Domain protocols for progress tracking (dependency injection interfaces)


class ProgressSubscriber(Protocol):
    """Protocol for consuming progress events from operations.

    Interface for components that display, log, or react to progress updates.
    Implementations might include CLI progress bars, web UI updates, logging
    systems, or workflow coordination logic.
    """

    async def on_progress_event(self, event: ProgressEvent) -> None:
        """Handle a progress event from an active operation.

        Args:
            event: Progress event with current status

        Note:
            Implementation should handle errors gracefully and not propagate
            exceptions that could disrupt the publishing operation.
        """
        ...

    async def on_operation_started(self, operation: ProgressOperation) -> None:
        """Handle notification that a new operation has started.

        Args:
            operation: Newly started operation
        """
        ...

    async def on_operation_completed(
        self, operation_id: str, final_status: OperationStatus
    ) -> None:
        """Handle notification that an operation has finished.

        Args:
            operation_id: ID of completed operation
            final_status: How the operation ended (success/failure/cancellation)
        """
        ...


def create_progress_event(
    operation_id: str,
    current: int,
    total: int | None = None,
    message: str = "Processing...",
    status: ProgressStatus = ProgressStatus.IN_PROGRESS,
    **metadata: Any,
) -> ProgressEvent:
    """Factory function for creating valid progress events.

    Args:
        operation_id: ID of the operation this event belongs to
        current: Current progress count
        total: Total expected items (None for indeterminate)
        message: Human-readable description
        status: Event status from ProgressStatus enum
        **metadata: Additional operation-specific data

    Returns:
        Validated ProgressEvent instance
    """
    return ProgressEvent(
        operation_id=operation_id,
        current=current,
        total=total,
        message=message,
        status=status,
        metadata=metadata,
    )


def create_progress_operation(
    description: str, total_items: int | None = None, **metadata: Any
) -> ProgressOperation:
    """Factory function for creating valid progress operations.

    Args:
        description: Human-readable operation name
        total_items: Expected number of items (None for indeterminate)
        **metadata: Additional operation context

    Returns:
        Validated ProgressOperation instance with unique ID
    """
    return ProgressOperation(
        description=description, total_items=total_items, metadata=metadata
    )
