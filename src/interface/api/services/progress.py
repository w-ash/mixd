"""SSE progress infrastructure for streaming operation updates to web clients.

Three components bridge the domain progress system to Server-Sent Events:

- OperationBoundEmitter: Decorator that pre-assigns an operation_id so the API
  can return it immediately while the background task runs.
- SSEOperationRegistry: Maps operation_id → asyncio.Queue for SSE consumers.
- SSEProgressSubscriber: ProgressSubscriber that routes events into SSE queues.
"""

import asyncio
from typing import Final, override

from attrs import define, field

from src.config import get_logger
from src.config.constants import WorkflowConstants
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
    """Wraps a ProgressEmitter, parenting use-case operations to one request op.

    The API layer pre-generates the request ``operation_id`` (the SSE queue key)
    and owns its lifecycle in ``run_sse_operation``.  Every operation a use case
    starts is reparented to that request op — it keeps its *own* id and routes as
    a sub-operation of the request.

    This replaced an earlier design that *rebound* every ``start_operation`` to the
    one request id.  Rebinding collapsed distinct operations (e.g. an importer's
    two phases, or its per-day chunks) onto a single coordinator entry, which then
    raised "already being tracked" / "progress went backwards" and silently aborted
    the import — the v0.8.5 SSE-seam data-loss bug.  ``emit_progress`` /
    ``complete_operation`` were always pass-through and stay so.
    """

    def __init__(self, delegate: ProgressEmitter, operation_id: str) -> None:
        self._delegate = delegate
        self._operation_id = operation_id

    @property
    def operation_id(self) -> str:
        """The request (parent) operation id this emitter parents children to.

        Exposed so a multi-level flow (connector-playlist import) can parent its
        per-item sub-operations directly to the request op — the SSE subscriber
        routes only one level, so the request op must be the single parent.
        """
        return self._operation_id

    @override
    async def start_operation(self, operation: ProgressOperation) -> str:
        # Already parented (e.g. an explicit sub-op) — forward untouched so we
        # never overwrite a deliberate parent or create a child-of-child the
        # single-level SSE subscriber can't route.
        if operation.metadata.get("parent_operation_id"):
            return await self._delegate.start_operation(operation)
        child = operation.with_metadata(parent_operation_id=self._operation_id)
        return await self._delegate.start_operation(child)

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


# ---------------------------------------------------------------------------
# SSEProgressSubscriber
# ---------------------------------------------------------------------------


@define(slots=True)
class SSEProgressSubscriber:
    """Routes progress events into per-operation SSE queues.

    Subscribed once to ProgressBroker at app startup.  When events
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
                "event": WorkflowConstants.SSE_EVENT_SUB_OPERATION_STARTED,
                "data": {
                    "operation_id": operation.operation_id,
                    "parent_operation_id": parent_id,
                    "description": operation.description,
                    "total": operation.total_items,
                    "phase": operation.metadata.get("phase"),
                    "node_type": operation.metadata.get("node_type"),
                    "connector_playlist_identifier": operation.metadata.get(
                        "connector_playlist_identifier"
                    ),
                    "playlist_name": operation.metadata.get("playlist_name"),
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
            "event": WorkflowConstants.SSE_EVENT_STARTED,
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
                    sub_metadata = event.metadata or {}
                    await queue.put({
                        "id": event_id,
                        "event": WorkflowConstants.SSE_EVENT_SUB_PROGRESS,
                        "data": {
                            "operation_id": event.operation_id,
                            "parent_operation_id": parent_id,
                            "current": event.current,
                            "total": event.total,
                            "message": event.message,
                            "status": event.status.value,
                            "completion_percentage": event.completion_percentage,
                            "phase": sub_metadata.get("phase"),
                            "outcome": sub_metadata.get("outcome"),
                            "resolved": sub_metadata.get("resolved"),
                            "unresolved": sub_metadata.get("unresolved"),
                            "canonical_playlist_id": sub_metadata.get(
                                "canonical_playlist_id"
                            ),
                            "connector_playlist_identifier": sub_metadata.get(
                                "connector_playlist_identifier"
                            ),
                            "playlist_name": sub_metadata.get("playlist_name"),
                            "error_message": sub_metadata.get("error_message"),
                        },
                    })
            return

        event_id = self._next_event_id(event.operation_id)
        metadata = event.metadata or {}

        await queue.put({
            "id": event_id,
            "event": WorkflowConstants.SSE_EVENT_PROGRESS,
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
            # A sub-operation completing — route to the parent queue. No sentinel:
            # only the top-level operation closes the stream.
            parent_id = self._sub_op_parents.pop(operation_id, None)
            if parent_id:
                parent_queue = await self._registry.get_queue(parent_id)
                if parent_queue is not None:
                    event_id = self._next_event_id(parent_id)
                    await parent_queue.put({
                        "id": event_id,
                        "event": WorkflowConstants.SSE_EVENT_SUB_OPERATION_COMPLETED,
                        "data": {
                            "operation_id": operation_id,
                            "parent_operation_id": parent_id,
                            "final_status": final_status.value,
                        },
                    })
            return

        # Top-level (registered) operation: the SSE seam owns the terminal event +
        # sentinel. ``run_sse_operation`` pushes ``build_terminal_event`` with the
        # ``OperationResult`` status + counts (the workflow/preview path does
        # the same directly) and ``finalize_sse_operation`` pushes the sentinel.
        # Emitting them here too would double the terminal event and counts-less it.
        # The subscriber only cleans up its per-op counter.
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
