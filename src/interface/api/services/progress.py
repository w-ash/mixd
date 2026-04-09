"""SSE progress infrastructure for streaming operation updates to web clients.

Three components bridge the domain progress system to Server-Sent Events:

- OperationBoundEmitter: Decorator that pre-assigns an operation_id so the API
  can return it immediately while the background task runs.
- SSEOperationRegistry: Maps operation_id → asyncio.Queue for SSE consumers.
- SSEProgressSubscriber: ProgressSubscriber that routes events into SSE queues.
"""

import asyncio
from typing import Final, override

from attrs import define, evolve, field

from src.config import get_logger
from src.domain.entities.progress import (
    OperationStatus,
    ProgressEmitter,
    ProgressEvent,
    ProgressOperation,
)

logger = get_logger(__name__).bind(service="sse_progress")


class _SSESentinel:
    """Typed sentinel placed on the queue to signal stream termination."""


SSE_SENTINEL: Final = _SSESentinel()


# ---------------------------------------------------------------------------
# OperationBoundEmitter
# ---------------------------------------------------------------------------


class OperationBoundEmitter(ProgressEmitter):
    """Wraps a ProgressEmitter, overriding operation_id on start_operation.

    The API layer pre-generates an operation_id so it can return the ID to
    the client before the background task begins emitting progress.  All
    three protocol methods are forwarded to the real emitter; only
    start_operation substitutes the ID.
    """

    def __init__(self, delegate: ProgressEmitter, operation_id: str) -> None:
        self._delegate = delegate
        self._operation_id = operation_id

    @override
    async def start_operation(self, operation: ProgressOperation) -> str:
        bound = evolve(operation, operation_id=self._operation_id)
        return await self._delegate.start_operation(bound)

    @override
    async def emit_progress(self, event: ProgressEvent) -> None:
        await self._delegate.emit_progress(event)

    @override
    async def complete_operation(
        self, operation_id: str, final_status: OperationStatus
    ) -> None:
        await self._delegate.complete_operation(operation_id, final_status)


# ---------------------------------------------------------------------------
# SSEOperationRegistry
# ---------------------------------------------------------------------------


class SSEOperationRegistry:
    """Maps operation_id → asyncio.Queue for SSE event delivery.

    Thread-safe via asyncio.Lock. The queue is the rendezvous point between
    background tasks (producers via SSEProgressSubscriber) and SSE endpoint
    generators (consumers).
    """

    def __init__(self) -> None:
        self._queues: dict[str, asyncio.Queue[object]] = {}
        self._lock = asyncio.Lock()

    async def register(self, operation_id: str) -> asyncio.Queue[object]:
        async with self._lock:
            queue: asyncio.Queue[object] = asyncio.Queue()
            self._queues[operation_id] = queue
            logger.debug("SSE queue registered", operation_id=operation_id)
            return queue

    async def get_queue(self, operation_id: str) -> asyncio.Queue[object] | None:
        async with self._lock:
            return self._queues.get(operation_id)

    async def unregister(self, operation_id: str) -> None:
        async with self._lock:
            removed = self._queues.pop(operation_id, None)
            if removed is not None:
                logger.debug("SSE queue unregistered", operation_id=operation_id)

    async def get_active_operation_ids(self) -> list[str]:
        async with self._lock:
            return list(self._queues.keys())


# ---------------------------------------------------------------------------
# SSEProgressSubscriber
# ---------------------------------------------------------------------------


@define(slots=True)
class SSEProgressSubscriber:
    """Routes progress events into per-operation SSE queues.

    Subscribed once to AsyncProgressManager at app startup.  When events
    arrive, looks up the operation_id in the registry and puts structured
    SSE event dicts into the matching queue.  Unknown operation_ids are
    silently ignored (the operation may not have come from the web UI).
    """

    _registry: SSEOperationRegistry
    _event_counters: dict[str, int] = field(factory=dict)
    _sub_op_parents: dict[str, str] = field(factory=dict)  # sub_op_id → parent_op_id

    async def on_operation_started(self, operation: ProgressOperation) -> None:
        parent_id = operation.metadata.get("parent_operation_id")

        # Sub-operations route to the parent's queue
        if isinstance(parent_id, str):
            queue = await self._registry.get_queue(parent_id)
            if queue is None:
                return

            # Track sub-operation → parent mapping for later routing
            self._sub_op_parents[operation.operation_id] = parent_id

            # Use parent's event counter for consistent sequencing
            event_id = self._next_event_id(parent_id)
            await queue.put({
                "id": event_id,
                "event": "sub_operation_started",
                "data": {
                    "operation_id": operation.operation_id,
                    "parent_operation_id": parent_id,
                    "description": operation.description,
                    "total": operation.total_items,
                    "phase": operation.metadata.get("phase"),
                    "node_type": operation.metadata.get("node_type"),
                    "status": operation.status.value,
                },
            })
            return

        queue = await self._registry.get_queue(operation.operation_id)
        if queue is None:
            return

        self._event_counters[operation.operation_id] = 0
        event_id = self._next_event_id(operation.operation_id)

        await queue.put({
            "id": event_id,
            "event": "started",
            "data": {
                "operation_id": operation.operation_id,
                "description": operation.description,
                "total": operation.total_items,
                "status": operation.status.value,
            },
        })

    async def on_progress_event(self, event: ProgressEvent) -> None:
        # Try direct queue first (normal operations)
        queue = await self._registry.get_queue(event.operation_id)

        if queue is None:
            # Check if this is a sub-operation — look up parent via tracked mapping
            parent_id = self._sub_op_parents.get(event.operation_id)
            if parent_id:
                queue = await self._registry.get_queue(parent_id)
                if queue is not None:
                    event_id = self._next_event_id(parent_id)
                    await queue.put({
                        "id": event_id,
                        "event": "sub_progress",
                        "data": {
                            "operation_id": event.operation_id,
                            "parent_operation_id": parent_id,
                            "current": event.current,
                            "total": event.total,
                            "message": event.message,
                            "status": event.status.value,
                            "completion_percentage": event.completion_percentage,
                        },
                    })
            return

        event_id = self._next_event_id(event.operation_id)
        metadata = event.metadata or {}

        await queue.put({
            "id": event_id,
            "event": "progress",
            "data": {
                "operation_id": event.operation_id,
                "current": event.current,
                "total": event.total,
                "message": event.message,
                "status": event.status.value,
                "completion_percentage": event.completion_percentage,
                "items_per_second": metadata.get("items_per_second"),
                "eta_seconds": metadata.get("eta_seconds"),
            },
        })

    async def on_operation_completed(
        self, operation_id: str, final_status: OperationStatus
    ) -> None:
        queue = await self._registry.get_queue(operation_id)

        if queue is None:
            # May be a sub-operation completing — route to parent
            parent_id = self._sub_op_parents.pop(operation_id, None)
            if parent_id:
                parent_queue = await self._registry.get_queue(parent_id)
                if parent_queue is not None:
                    event_id = self._next_event_id(parent_id)
                    await parent_queue.put({
                        "id": event_id,
                        "event": "sub_operation_completed",
                        "data": {
                            "operation_id": operation_id,
                            "parent_operation_id": parent_id,
                            "final_status": final_status.value,
                        },
                    })
            return

        event_id = self._next_event_id(operation_id)
        event_type = "error" if final_status == OperationStatus.FAILED else "complete"

        await queue.put({
            "id": event_id,
            "event": event_type,
            "data": {
                "operation_id": operation_id,
                "final_status": final_status.value,
            },
        })

        # Sentinel tells the SSE generator to close the connection
        await queue.put(SSE_SENTINEL)

        # Cleanup counter
        self._event_counters.pop(operation_id, None)

    def _next_event_id(self, operation_id: str) -> str:
        count = self._event_counters.get(operation_id, 0) + 1
        self._event_counters[operation_id] = count
        return f"evt_{count}"


# ---------------------------------------------------------------------------
# Module-level singletons
# ---------------------------------------------------------------------------

_global_registry: SSEOperationRegistry | None = None


def get_operation_registry() -> SSEOperationRegistry:
    """Get the global SSE operation registry instance."""
    global _global_registry
    if _global_registry is None:
        _global_registry = SSEOperationRegistry()
    return _global_registry
