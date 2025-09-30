"""Application service for managing progress tracking across operations.

Orchestrates progress events between domain services and interface providers.
Handles subscriber management, error isolation, and coordinates with the
ProgressCoordinator domain service for business rule enforcement.
"""

import asyncio
from typing import Any
from uuid import uuid4

from attrs import define

from src.config import get_logger
from src.domain.entities.progress import (
    OperationStatus,
    ProgressEvent,
    ProgressOperation,
    ProgressSubscriber,
)
from src.domain.services.progress_coordinator import ProgressCoordinator

logger = get_logger(__name__).bind(service="progress_manager")


@define(slots=True)
class SubscriberRegistration:
    """Internal tracking for a registered progress subscriber."""

    subscriber_id: str
    subscriber: ProgressSubscriber
    is_active: bool = True


class AsyncProgressManager:
    """Application service orchestrating progress tracking across the system.

    Implements the ProgressEmitter protocol and manages ProgressSubscriber instances.
    Coordinates with domain services for business rule validation and provides
    error isolation to prevent subscriber failures from disrupting operations.

    This service acts as the central hub for all progress tracking in the application,
    bridging the domain layer (business rules) with the interface layer (display).
    """

    def __init__(self):
        """Initialize progress manager."""
        self._coordinator = ProgressCoordinator()
        self._subscribers: dict[str, SubscriberRegistration] = {}
        self._subscriber_lock = asyncio.Lock()
        self._event_tasks: set[asyncio.Task] = set()

        # Contextual logger
        self._logger = logger.bind(manager_id=str(uuid4())[:8])

        self._logger.info("AsyncProgressManager initialized")

    async def subscribe(self, subscriber: ProgressSubscriber) -> str:
        """Register a progress subscriber to receive events.

        Args:
            subscriber: Subscriber implementation to register

        Returns:
            Unique subscription ID for later unsubscription
        """
        subscriber_id = str(uuid4())

        async with self._subscriber_lock:
            registration = SubscriberRegistration(
                subscriber_id=subscriber_id, subscriber=subscriber
            )
            self._subscribers[subscriber_id] = registration

        self._logger.info(
            "Progress subscriber registered",
            subscriber_id=subscriber_id,
            subscriber_type=type(subscriber).__name__,
            total_subscribers=len(self._subscribers),
        )

        return subscriber_id

    async def unsubscribe(self, subscriber_id: str) -> bool:
        """Unregister a progress subscriber.

        Args:
            subscriber_id: ID returned from subscribe()

        Returns:
            True if subscriber was found and removed, False otherwise
        """
        async with self._subscriber_lock:
            registration = self._subscribers.get(subscriber_id)
            if registration is None:
                self._logger.warning(
                    "Attempt to unsubscribe unknown subscriber",
                    subscriber_id=subscriber_id,
                )
                return False

            # Mark as inactive and remove
            registration.is_active = False
            del self._subscribers[subscriber_id]

        self._logger.info(
            "Progress subscriber unregistered",
            subscriber_id=subscriber_id,
            remaining_subscribers=len(self._subscribers),
        )

        return True

    async def emit_progress(self, event: ProgressEvent) -> None:
        """Emit a progress event to all registered subscribers.

        Validates the event through domain service and notifies all active
        subscribers. Subscriber errors are isolated and logged but do not
        disrupt the publishing operation.

        Args:
            event: Progress event to publish

        Raises:
            ValueError: If event fails domain validation
        """
        try:
            # Validate and record event through domain service
            validated_event = await self._coordinator.record_progress_event(event)

            # Notify all active subscribers
            await self._notify_subscribers("progress_event", validated_event)

        except ValueError as e:
            self._logger.warning(
                "Progress event validation failed",
                operation_id=event.operation_id,
                error=str(e),
            )
            raise

    async def start_operation(self, operation: ProgressOperation) -> str:
        """Begin tracking a new operation and notify subscribers.

        Args:
            operation: Operation to begin tracking

        Returns:
            The operation_id for subsequent progress events

        Raises:
            ValueError: If operation validation fails
        """
        try:
            # Start operation tracking through domain service
            running_operation = await self._coordinator.start_operation(operation)

            self._logger.info(
                "Operation started",
                operation_id=operation.operation_id,
                description=operation.description,
                total_items=operation.total_items,
                is_determinate=operation.is_determinate,
            )

            # Notify subscribers
            await self._notify_subscribers("operation_started", running_operation)

            return running_operation.operation_id

        except ValueError as e:
            self._logger.error(
                "Failed to start operation",
                operation_id=operation.operation_id,
                error=str(e),
            )
            raise

    async def complete_operation(
        self, operation_id: str, final_status: OperationStatus
    ) -> None:
        """Mark operation as complete and notify subscribers.

        Args:
            operation_id: ID of operation to complete
            final_status: Final status (COMPLETED, FAILED, or CANCELLED)

        Raises:
            ValueError: If operation is not found or invalid
        """
        try:
            # Complete operation through domain service
            completed_operation = await self._coordinator.complete_operation(
                operation_id, final_status
            )

            self._logger.info(
                "Operation completed",
                operation_id=operation_id,
                final_status=final_status.value,
                duration_seconds=completed_operation.duration_seconds,
            )

            # Notify subscribers
            await self._notify_subscribers(
                "operation_completed", operation_id, final_status
            )

        except ValueError as e:
            self._logger.error(
                "Failed to complete operation",
                operation_id=operation_id,
                final_status=final_status.value,
                error=str(e),
            )
            raise

    async def get_operation(self, operation_id: str) -> ProgressOperation | None:
        """Get current state of an operation.

        Args:
            operation_id: ID of operation to retrieve

        Returns:
            Current operation state or None if not found
        """
        return await self._coordinator.get_operation(operation_id)

    async def get_active_operations(self) -> list[ProgressOperation]:
        """Get all currently active (running) operations.

        Returns:
            List of operations with RUNNING status
        """
        return await self._coordinator.get_active_operations()

    async def cleanup_completed_operations(
        self, max_age_seconds: float = 3600.0
    ) -> int:
        """Clean up old completed operations from tracking state.

        Args:
            max_age_seconds: Maximum age in seconds for completed operations

        Returns:
            Number of operations cleaned up
        """
        cleanup_count = await self._coordinator.cleanup_completed_operations(
            max_age_seconds
        )

        if cleanup_count > 0:
            self._logger.info(
                "Cleaned up completed operations",
                cleanup_count=cleanup_count,
                max_age_seconds=max_age_seconds,
            )

        return cleanup_count

    async def shutdown(self) -> None:
        """Gracefully shutdown the progress manager.

        Cancels any pending notification tasks and clears subscribers.
        """
        self._logger.info("Shutting down AsyncProgressManager")

        # Cancel any pending notification tasks
        for task in self._event_tasks:
            if not task.done():
                task.cancel()

        # Wait for tasks to complete cancellation
        if self._event_tasks:
            await asyncio.gather(*self._event_tasks, return_exceptions=True)

        # Clear subscribers
        async with self._subscriber_lock:
            subscriber_count = len(self._subscribers)
            self._subscribers.clear()

        self._logger.info(
            "AsyncProgressManager shutdown complete",
            cancelled_tasks=len(self._event_tasks),
            removed_subscribers=subscriber_count,
        )

    async def _notify_subscribers(self, notification_type: str, *args: Any) -> None:
        """Notify all active subscribers with error isolation.

        Args:
            notification_type: Type of notification ('progress_event', 'operation_started', etc.)
            *args: Arguments to pass to subscriber methods
        """
        async with self._subscriber_lock:
            active_subscribers = [
                registration
                for registration in self._subscribers.values()
                if registration.is_active
            ]

        if not active_subscribers:
            return

        # Create notification tasks for all subscribers
        notification_tasks = []
        for registration in active_subscribers:
            task = asyncio.create_task(
                self._safe_notify_subscriber(registration, notification_type, *args),
                name=f"notify_{registration.subscriber_id}_{notification_type}",
            )
            notification_tasks.append(task)

        # Track tasks and run them concurrently
        self._event_tasks.update(notification_tasks)

        try:
            # Run all notifications concurrently
            await asyncio.gather(*notification_tasks, return_exceptions=True)
        finally:
            # Clean up completed tasks
            for task in notification_tasks:
                self._event_tasks.discard(task)

    async def _safe_notify_subscriber(
        self, registration: SubscriberRegistration, notification_type: str, *args: Any
    ) -> None:
        """Safely notify a single subscriber with error handling.

        Args:
            registration: Subscriber registration to notify
            notification_type: Type of notification to send
            *args: Arguments for the notification
        """
        if not registration.is_active:
            return

        try:
            subscriber = registration.subscriber

            if notification_type == "progress_event":
                await subscriber.on_progress_event(args[0])
            elif notification_type == "operation_started":
                await subscriber.on_operation_started(args[0])
            elif notification_type == "operation_completed":
                await subscriber.on_operation_completed(args[0], args[1])
            else:
                self._logger.warning(
                    "Unknown notification type",
                    notification_type=notification_type,
                    subscriber_id=registration.subscriber_id,
                )

        except Exception as e:
            # Log subscriber error but don't propagate - isolation is critical
            self._logger.error(
                "Subscriber notification failed",
                subscriber_id=registration.subscriber_id,
                subscriber_type=type(registration.subscriber).__name__,
                notification_type=notification_type,
                error=str(e),
                error_type=type(e).__name__,
            )

            # Consider marking subscriber as inactive on repeated failures
            # This could be enhanced with a failure count and automatic cleanup

    @property
    def subscriber_count(self) -> int:
        """Get current number of active subscribers."""
        return len(self._subscribers)

    @property
    def coordinator(self) -> ProgressCoordinator:
        """Access to underlying domain coordinator (for testing/debugging)."""
        return self._coordinator


# Singleton instance for application-wide progress tracking
_global_progress_manager: AsyncProgressManager | None = None


def get_progress_manager() -> AsyncProgressManager:
    """Get the global progress manager instance.

    Creates a new instance if none exists. Use this for dependency injection
    in application services and use cases.

    Returns:
        Shared AsyncProgressManager instance
    """
    global _global_progress_manager
    if _global_progress_manager is None:
        _global_progress_manager = AsyncProgressManager()
    return _global_progress_manager


def set_progress_manager(manager: AsyncProgressManager) -> None:
    """Set a custom global progress manager instance.

    Useful for testing or custom configuration scenarios.

    Args:
        manager: Progress manager instance to use globally
    """
    global _global_progress_manager
    _global_progress_manager = manager


async def shutdown_global_progress_manager() -> None:
    """Shutdown the global progress manager if it exists.

    Should be called during application shutdown to clean up resources.
    """
    global _global_progress_manager
    if _global_progress_manager is not None:
        await _global_progress_manager.shutdown()
        _global_progress_manager = None


# --- Protocol Adapter ---


@define(slots=True)
class AsyncProgressManagerAdapter:
    """Adapter that makes AsyncProgressManager implement ProgressEmitter protocol.

    Provides a clean interface for dependency injection while maintaining type safety.
    Eliminates the need for None checks and union types throughout the codebase.
    """

    _manager: AsyncProgressManager

    async def start_operation(self, operation: ProgressOperation) -> str:
        """Start tracking an operation via the progress manager."""
        return await self._manager.start_operation(operation)

    async def emit_progress(self, event: ProgressEvent) -> None:
        """Emit a progress event via the progress manager."""
        await self._manager.emit_progress(event)

    async def complete_operation(
        self, operation_id: str, final_status: OperationStatus
    ) -> None:
        """Complete an operation via the progress manager."""
        await self._manager.complete_operation(operation_id, final_status)
