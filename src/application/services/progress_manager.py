"""Application service for managing progress tracking across operations.

Orchestrates progress events between domain services and interface providers.
Handles subscriber management, error isolation, and coordinates with the
ProgressCoordinator domain service for business rule enforcement.
"""

# Legitimate Any: use case results, OperationResult metadata, metric values

import asyncio
from asyncio import CancelledError
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING
from uuid import uuid4

from attrs import define

from src.config import get_logger

if TYPE_CHECKING:
    from structlog.stdlib import BoundLogger

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

    _coordinator: ProgressCoordinator
    _subscriber_lock: asyncio.Lock
    _logger: BoundLogger

    def __init__(self):
        """Initialize progress manager."""
        self._coordinator = ProgressCoordinator()
        self._subscribers: dict[str, SubscriberRegistration] = {}
        self._subscriber_lock = asyncio.Lock()

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
            await self._broadcast(lambda s: s.on_progress_event(validated_event))

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
            await self._broadcast(lambda s: s.on_operation_started(running_operation))

        except ValueError as e:
            self._logger.error(
                "Failed to start operation",
                operation_id=operation.operation_id,
                error=str(e),
            )
            raise
        else:
            return running_operation.operation_id

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
            await self._broadcast(
                lambda s: s.on_operation_completed(operation_id, final_status)
            )

        except ValueError as e:
            self._logger.error(
                "Failed to complete operation",
                operation_id=operation_id,
                final_status=final_status.value,
                error=str(e),
            )
            raise

    async def _broadcast(
        self,
        notify_fn: Callable[[ProgressSubscriber], Awaitable[None]],
    ) -> None:
        """Broadcast to all active subscribers with error isolation.

        gather(return_exceptions=True) instead of TaskGroup: subscriber
        failures must NEVER propagate to the publishing operation.
        TaskGroup propagates BaseException (including CancelledError from
        Prefect cancel scopes), violating the isolation contract.

        CancelledError at the gather() site (injected by Prefect cancel scope
        or uvicorn reload) is caught and cleared via task.uncancel() —
        subscriber notification is fire-and-forget and must never kill the
        publishing workflow.
        """
        async with self._subscriber_lock:
            active = [r for r in self._subscribers.values() if r.is_active]

        if not active:
            return

        try:
            results = await asyncio.gather(
                *(self._safe_call(r, notify_fn) for r in active),
                return_exceptions=True,
            )
        except CancelledError:
            # CancelledError at the gather() site means the parent task was
            # cancelled externally — NOT a subscriber-internal failure.
            # Subscriber notification is fire-and-forget: it must never kill
            # the publishing workflow. Clear the cancel request so it doesn't
            # re-raise at the caller's next await point. Genuine cancellation
            # (server shutdown) will re-cancel from the external source.
            self._logger.debug(
                "Subscriber notification interrupted by task cancellation",
            )
            task = asyncio.current_task()
            if task is not None:
                task.uncancel()
            return

        for result in results:
            if isinstance(result, BaseException):
                self._logger.error(
                    "Subscriber notification raised unexpectedly",
                    error=str(result),
                    error_type=type(result).__name__,
                )

    async def _safe_call(
        self,
        registration: SubscriberRegistration,
        notify_fn: Callable[[ProgressSubscriber], Awaitable[None]],
    ) -> None:
        """Safely call a subscriber with error isolation."""
        if not registration.is_active:
            return

        try:
            await notify_fn(registration.subscriber)
        except (CancelledError, Exception) as e:
            self._logger.error(
                "Subscriber notification failed",
                subscriber_id=registration.subscriber_id,
                subscriber_type=type(registration.subscriber).__name__,
                error=str(e),
                error_type=type(e).__name__,
            )

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
