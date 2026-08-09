"""SSE progress infrastructure for streaming operation updates to web clients.

Three components bridge the domain progress system to Server-Sent Events:

- OperationBoundEmitter: Decorator that pre-assigns an operation_id so the API
  can return it immediately while the background task runs.
- SSEOperationRegistry: Maps operation_id → asyncio.Queue for SSE consumers.
- SSEProgressSubscriber: ProgressSubscriber that routes events into SSE queues.
"""

import asyncio
from typing import Final, override
from uuid import UUID

from attrs import define

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

    def __init__(
        self,
        delegate: ProgressEmitter,
        operation_id: str,
        run_id: UUID | None = None,
    ) -> None:
        self._delegate = delegate
        self._operation_id = operation_id
        self._run_id = run_id

    @property
    def operation_id(self) -> str:
        """The request (parent) operation id this emitter parents children to.

        Exposed so a multi-level flow (connector-playlist import) can parent its
        per-item sub-operations directly to the request op.
        """
        return self._operation_id

    @property
    def run_id(self) -> UUID | None:
        """The ``OperationRun`` audit-row id for this request, when one exists.

        Threaded so a use case can record per-item issues (``append_run_issue``)
        against the durable audit row. ``None`` on paths that don't write a row
        (CLI, tests), so issue recording is simply skipped there.
        """
        return self._run_id

    @override
    async def start_operation(self, operation: ProgressOperation) -> str:
        # Already parented (e.g. an explicit sub-op) — forward untouched rather
        # than overwrite a deliberate parent.
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


@define(frozen=True, slots=True)
class AncestorStream:
    """One registered stream an event should also appear on.

    ``item_operation_id`` is the operation directly beneath
    ``stream_operation_id`` — "which row does this belong to". A drain watching
    thirteen files gets the file's id even for an event two levels down, so the
    client never reconstructs the tree.
    """

    queue: asyncio.Queue[object]
    stream_operation_id: str
    item_operation_id: str


class SSEOperationRegistry:
    """Maps operation_id → asyncio.Queue for SSE event delivery.

    Also owns the operation tree (child → parent edges) and the per-stream
    event-id counters: both are routing state with two producers
    (``SSEProgressSubscriber`` and ``sse_operations``), so they live where both
    already reach.

    Thread-safe via asyncio.Lock. The queue is the rendezvous point between
    background tasks (producers) and SSE endpoint generators (consumers).
    """

    def __init__(self) -> None:
        self._queues: dict[str, asyncio.Queue[object]] = {}
        self._parents: dict[str, str] = {}
        self._event_counters: dict[str, int] = {}
        self._lock = asyncio.Lock()

    async def register(self, operation_id: str) -> asyncio.Queue[object]:
        async with self._lock:
            queue: asyncio.Queue[object] = asyncio.Queue()
            self._queues[operation_id] = queue
            self._event_counters[operation_id] = 0
            logger.debug("SSE queue registered", operation_id=operation_id)
            return queue

    async def get_queue(self, operation_id: str) -> asyncio.Queue[object] | None:
        async with self._lock:
            return self._queues.get(operation_id)

    async def unregister(self, operation_id: str) -> None:
        async with self._lock:
            removed = self._queues.pop(operation_id, None)
            _ = self._event_counters.pop(operation_id, None)
            _ = self._parents.pop(operation_id, None)
            if removed is not None:
                logger.debug("SSE queue unregistered", operation_id=operation_id)

    async def record_parent(self, operation_id: str, parent_operation_id: str) -> None:
        """Remember one child → parent edge.

        Recorded even when the parent is unregistered — it is still the link a
        grandchild walks through to reach a stream above it.
        """
        async with self._lock:
            self._parents[operation_id] = parent_operation_id

    async def forget_parent(self, operation_id: str) -> None:
        async with self._lock:
            _ = self._parents.pop(operation_id, None)

    async def ancestor_streams(self, operation_id: str) -> list[AncestorStream]:
        """Every registered ancestor stream, nearest first.

        Fan-out rather than nearest-only: a queued file belongs on its own
        stream *and* on the drain's, and neither consumer should depend on who
        else is listening. ``seen`` bounds the walk, since parent ids are
        caller-supplied metadata and a cycle must not hang the loop.
        """
        async with self._lock:
            targets: list[AncestorStream] = []
            seen = {operation_id}
            child = operation_id
            while (parent := self._parents.get(child)) is not None:
                if parent in seen:
                    break
                seen.add(parent)
                queue = self._queues.get(parent)
                if queue is not None:
                    targets.append(
                        AncestorStream(
                            queue=queue,
                            stream_operation_id=parent,
                            item_operation_id=child,
                        )
                    )
                child = parent
            return targets

    async def next_event_id(self, stream_operation_id: str) -> str:
        """The next ``evt_<n>`` for one stream.

        Central because ``Last-Event-ID`` resume filters on the number: with two
        producers writing one stream, locally-kept counters would collide.
        """
        async with self._lock:
            count = self._event_counters.get(stream_operation_id, 0) + 1
            self._event_counters[stream_operation_id] = count
            return f"evt_{count}"


# ---------------------------------------------------------------------------
# SSEProgressSubscriber
# ---------------------------------------------------------------------------


@define(slots=True)
class SSEProgressSubscriber:
    """Routes progress events into per-operation SSE queues.

    Subscribed once to ProgressBroker at app startup. An operation's events go
    to its own registered stream (as ``started``/``progress``) *and* to every
    registered ancestor's stream (as ``sub_*``). Unknown operation_ids are
    silently ignored — the operation may not have come from the web UI.

    Routing walks the whole chain, not just one level: a queued file's phase
    sits two below the drain, and the drain is the surface that wants to render
    where that file has got to.
    """

    _registry: SSEOperationRegistry

    async def on_operation_started(self, operation: ProgressOperation) -> None:
        parent_id = operation.metadata.get("parent_operation_id")
        if isinstance(parent_id, str):
            # Before any delivery decision: an unregistered parent is still a
            # load-bearing edge for its own children.
            await self._registry.record_parent(operation.operation_id, parent_id)

        own_queue = await self._registry.get_queue(operation.operation_id)
        if own_queue is not None:
            await self._put(
                own_queue,
                operation.operation_id,
                WorkflowConstants.SSE_EVENT_STARTED,
                {
                    "operation_id": operation.operation_id,
                    "description": operation.description,
                    "total": operation.total_items,
                    "status": operation.status.value,
                },
            )

        await self._fan_out(
            operation.operation_id,
            WorkflowConstants.SSE_EVENT_SUB_OPERATION_STARTED,
            {
                "operation_id": operation.operation_id,
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
        )

    async def on_progress_event(self, event: ProgressEvent) -> None:
        metadata = event.metadata or {}

        own_queue = await self._registry.get_queue(event.operation_id)
        if own_queue is not None:
            await self._put(
                own_queue,
                event.operation_id,
                WorkflowConstants.SSE_EVENT_PROGRESS,
                {
                    "operation_id": event.operation_id,
                    "current": event.current,
                    "total": event.total,
                    "message": event.message,
                    "status": event.status.value,
                    "completion_percentage": event.completion_percentage,
                    "items_per_second": metadata.get("items_per_second"),
                    "eta_seconds": metadata.get("eta_seconds"),
                },
            )

        await self._fan_out(
            event.operation_id,
            WorkflowConstants.SSE_EVENT_SUB_PROGRESS,
            {
                "operation_id": event.operation_id,
                "current": event.current,
                "total": event.total,
                "message": event.message,
                "status": event.status.value,
                "completion_percentage": event.completion_percentage,
                "phase": metadata.get("phase"),
                "outcome": metadata.get("outcome"),
                "resolved": metadata.get("resolved"),
                "unresolved": metadata.get("unresolved"),
                "canonical_playlist_id": metadata.get("canonical_playlist_id"),
                "connector_playlist_identifier": metadata.get(
                    "connector_playlist_identifier"
                ),
                "playlist_name": metadata.get("playlist_name"),
                "error_message": metadata.get("error_message"),
            },
        )

    async def on_operation_completed(
        self, operation_id: str, final_status: OperationStatus
    ) -> None:
        """Announce an unregistered sub-operation's end; drop its tree edge.

        A registered operation's terminal belongs to the SSE seam, which has the
        counts this does not; announcing it here too would double the event and
        strip them off the copy. The edge drops either way — nothing else
        unregisters an operation that never had a stream.
        """
        if await self._registry.get_queue(operation_id) is None:
            # No sentinel: only the top-level operation closes a stream.
            await self._fan_out(
                operation_id,
                WorkflowConstants.SSE_EVENT_SUB_OPERATION_COMPLETED,
                {
                    "operation_id": operation_id,
                    "final_status": final_status.value,
                },
            )
        await self._registry.forget_parent(operation_id)

    async def _fan_out(
        self, operation_id: str, event_type: str, data: dict[str, object]
    ) -> None:
        """Deliver one event to every registered ancestor stream."""
        for target in await self._registry.ancestor_streams(operation_id):
            await self._put(
                target.queue,
                target.stream_operation_id,
                event_type,
                {
                    **data,
                    # The stream's owner, which is what this has always meant to
                    # a consumer — so single-level payloads stay byte-identical
                    # and ``item_operation_id`` carries the extra generation.
                    "parent_operation_id": target.stream_operation_id,
                    "item_operation_id": target.item_operation_id,
                },
            )

    async def _put(
        self,
        queue: asyncio.Queue[object],
        stream_operation_id: str,
        event_type: str,
        data: dict[str, object],
    ) -> None:
        event_id = await self._registry.next_event_id(stream_operation_id)
        await queue.put({"id": event_id, "event": event_type, "data": data})


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
