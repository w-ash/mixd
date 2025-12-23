"""Domain service for coordinating progress tracking across operations.

Implements business rules for progress validation, operation lifecycle management,
and derived metric calculations. Ensures progress monotonicity, prevents event
flooding, and maintains operation state consistency.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from attrs import define, field

from src.domain.entities import utc_now_factory
from src.domain.entities.progress import (
    OperationStatus,
    ProgressEvent,
    ProgressOperation,
    create_progress_event,
)


@define(slots=True)
class OperationState:
    """Internal state tracking for a single operation."""

    operation: ProgressOperation
    last_current: int = 0
    last_event_time: datetime = field(factory=utc_now_factory)
    event_count: int = 0

    @property
    def is_active(self) -> bool:
        """Check if operation is still actively running."""
        return self.operation.status == OperationStatus.RUNNING


@define(slots=True)
class ProgressRateLimit:
    """Rate limiting configuration for progress events."""

    max_events_per_second: float = 10.0  # Maximum events per second per operation
    min_interval_seconds: float = field(init=False)

    def __attrs_post_init__(self) -> None:
        """Calculate minimum interval from rate limit."""
        self.min_interval_seconds = 1.0 / self.max_events_per_second


class ProgressCoordinator:
    """Domain service for coordinating progress events and operations.

    Enforces business rules like progress monotonicity, manages operation lifecycle,
    calculates derived metrics, and provides rate limiting to prevent event flooding.

    This is a pure domain service with no external dependencies - it only contains
    business logic and operates on domain entities.
    """

    def __init__(self):
        """Initialize progress coordinator."""
        self._operations: dict[str, OperationState] = {}
        self._operation_lock = asyncio.Lock()

    async def start_operation(self, operation: ProgressOperation) -> ProgressOperation:
        """Begin tracking a new operation with validation.

        Args:
            operation: Operation to begin tracking

        Returns:
            Operation with status updated to RUNNING

        Raises:
            ValueError: If operation is already being tracked
        """
        async with self._operation_lock:
            if operation.operation_id in self._operations:
                raise ValueError(
                    f"Operation {operation.operation_id} is already being tracked"
                )

            # Start the operation
            running_operation = operation.with_status(OperationStatus.RUNNING)
            self._operations[operation.operation_id] = OperationState(
                operation=running_operation,
                last_current=0,
                last_event_time=datetime.now(UTC)
                - timedelta(seconds=1.0),  # Allow first event immediately
                event_count=0,
            )

            return running_operation

    async def validate_progress_event(
        self, event: ProgressEvent
    ) -> tuple[bool, str | None]:
        """Validate a progress event against business rules.

        Args:
            event: Progress event to validate

        Returns:
            Tuple of (is_valid, error_message). error_message is None if valid.
        """
        async with self._operation_lock:
            operation_state = self._operations.get(event.operation_id)

            if operation_state is None:
                return False, f"No active operation found with ID {event.operation_id}"

            if not operation_state.is_active:
                return (
                    False,
                    f"Operation {event.operation_id} is not active (status: {operation_state.operation.status})",
                )

            # Check progress monotonicity (progress should not go backwards)
            if event.current < operation_state.last_current:
                return False, (
                    f"Progress went backwards: {event.current} < {operation_state.last_current} "
                    f"for operation {event.operation_id}"
                )

            # Rate limiting removed for simplicity

            return True, None

    async def record_progress_event(self, event: ProgressEvent) -> ProgressEvent:
        """Record and validate a progress event, updating operation state.

        Args:
            event: Progress event to record

        Returns:
            The validated event (potentially with calculated metadata)

        Raises:
            ValueError: If event fails validation
        """
        # Validate the event first
        is_valid, error_message = await self.validate_progress_event(event)
        if not is_valid:
            raise ValueError(f"Invalid progress event: {error_message}")

        async with self._operation_lock:
            operation_state = self._operations[event.operation_id]

            # Calculate derived metrics
            derived_metadata = await self._calculate_derived_metrics(
                event, operation_state
            )

            # Create enhanced event with derived metrics
            enhanced_event = create_progress_event(
                operation_id=event.operation_id,
                current=event.current,
                total=event.total,
                message=event.message,
                status=event.status,
                **event.metadata,
                **derived_metadata,
            )

            # Update operation state
            operation_state.last_current = event.current
            operation_state.last_event_time = datetime.now(UTC)
            operation_state.event_count += 1

            return enhanced_event

    async def complete_operation(
        self, operation_id: str, final_status: OperationStatus
    ) -> ProgressOperation:
        """Mark an operation as complete and clean up tracking state.

        Args:
            operation_id: ID of operation to complete
            final_status: Final operation status

        Returns:
            Completed operation with updated status and end time

        Raises:
            ValueError: If operation is not found or already complete
        """
        async with self._operation_lock:
            operation_state = self._operations.get(operation_id)

            if operation_state is None:
                raise ValueError(f"No operation found with ID {operation_id}")

            if operation_state.operation.is_complete:
                raise ValueError(f"Operation {operation_id} is already complete")

            # Complete the operation
            end_time = datetime.now(UTC)
            completed_operation = operation_state.operation.with_status(
                final_status, end_time
            )

            # Update internal state
            operation_state.operation = completed_operation

            return completed_operation

    async def get_operation(self, operation_id: str) -> ProgressOperation | None:
        """Retrieve current state of an operation.

        Args:
            operation_id: ID of operation to retrieve

        Returns:
            Current operation state or None if not found
        """
        async with self._operation_lock:
            operation_state = self._operations.get(operation_id)
            return operation_state.operation if operation_state else None

    async def get_active_operations(self) -> list[ProgressOperation]:
        """Get all currently active (running) operations.

        Returns:
            List of operations with RUNNING status
        """
        async with self._operation_lock:
            return [
                state.operation
                for state in self._operations.values()
                if state.is_active
            ]

    async def cleanup_completed_operations(
        self, max_age_seconds: float = 3600.0
    ) -> int:
        """Remove old completed operations from tracking state.

        Args:
            max_age_seconds: Maximum age in seconds for completed operations

        Returns:
            Number of operations cleaned up
        """
        cutoff_time = datetime.now(UTC).timestamp() - max_age_seconds
        cleanup_count = 0

        async with self._operation_lock:
            operations_to_remove = []

            for operation_id, state in self._operations.items():
                if (
                    state.operation.is_complete
                    and state.operation.end_time is not None
                    and state.operation.end_time.timestamp() < cutoff_time
                ):
                    operations_to_remove.append(operation_id)

            for operation_id in operations_to_remove:
                del self._operations[operation_id]
                cleanup_count += 1

        return cleanup_count

    async def _calculate_derived_metrics(
        self, event: ProgressEvent, operation_state: OperationState
    ) -> dict[str, Any]:
        """Calculate derived metrics for a progress event.

        Args:
            event: Current progress event
            operation_state: Current operation state

        Returns:
            Dictionary of derived metrics to add to event metadata
        """
        metrics = {}

        # Calculate rate (items per second) if we have enough data
        if operation_state.event_count > 0:
            elapsed_seconds = (
                datetime.now(UTC) - operation_state.operation.start_time
            ).total_seconds()

            if elapsed_seconds > 0:
                items_per_second = event.current / elapsed_seconds
                metrics["items_per_second"] = round(items_per_second, 2)

                # Calculate ETA if we know the total
                if event.total is not None and items_per_second > 0:
                    remaining_items = event.total - event.current
                    eta_seconds = remaining_items / items_per_second
                    metrics["eta_seconds"] = round(eta_seconds, 1)

        # Add event sequence number
        metrics["event_sequence"] = operation_state.event_count + 1

        # Add progress percentage if deterministic
        if event.completion_percentage is not None:
            metrics["completion_percentage"] = event.completion_percentage

        return metrics
