"""Unit tests for SSE progress infrastructure.

Tests OperationBoundEmitter, SSEOperationRegistry, and SSEProgressSubscriber
using mock progress managers and in-memory queues.
"""

import asyncio
from unittest.mock import AsyncMock

from src.domain.entities.progress import (
    OperationStatus,
    ProgressOperation,
    create_progress_event,
    create_progress_operation,
)
from src.interface.api.services.progress import (
    OperationBoundEmitter,
    SSEOperationRegistry,
    SSEProgressSubscriber,
)

# ---------------------------------------------------------------------------
# OperationBoundEmitter
# ---------------------------------------------------------------------------


class TestOperationBoundEmitter:
    """Tests that the emitter parents use-case operations to the request op.

    The emitter used to *rebind* every operation to one request id, collapsing
    distinct operations (an importer's phases / per-day chunks) onto a single
    coordinator entry — which raised "already being tracked" / "progress went
    backwards" and silently failed web imports (the v0.8.5 data-loss bug). It now
    keeps each operation's own id and injects ``parent_operation_id`` so they route
    as sub-operations of the request.
    """

    async def test_start_operation_injects_parent_metadata(self):
        delegate = AsyncMock()
        delegate.start_operation = AsyncMock(return_value="child-own-id")
        emitter = OperationBoundEmitter(delegate, operation_id="request-id")

        operation = create_progress_operation("Test operation")
        result = await emitter.start_operation(operation)

        # The delegate returns the child's OWN id — no rebinding.
        assert result == "child-own-id"
        call_args = delegate.start_operation.call_args[0][0]
        assert call_args.operation_id == operation.operation_id
        assert call_args.metadata["parent_operation_id"] == "request-id"
        assert call_args.description == "Test operation"

    async def test_start_operation_preserves_existing_parent(self):
        # An already-parented op (an explicit sub-op) is forwarded untouched so we
        # never overwrite a deliberate parent or build a child-of-child the
        # single-level subscriber can't route.
        delegate = AsyncMock()
        delegate.start_operation = AsyncMock(return_value="sub-id")
        emitter = OperationBoundEmitter(delegate, operation_id="request-id")

        operation = ProgressOperation(
            description="Sub op",
            metadata={"parent_operation_id": "other-parent"},
        )
        await emitter.start_operation(operation)

        call_args = delegate.start_operation.call_args[0][0]
        assert call_args.metadata["parent_operation_id"] == "other-parent"

    async def test_emit_progress_passes_through(self):
        delegate = AsyncMock()
        emitter = OperationBoundEmitter(delegate, operation_id="op-123")

        event = create_progress_event("op-123", current=5, total=10, message="Working")
        await emitter.emit_progress(event)

        delegate.emit_progress.assert_awaited_once_with(event)

    async def test_complete_operation_passes_through(self):
        delegate = AsyncMock()
        emitter = OperationBoundEmitter(delegate, operation_id="op-123")

        await emitter.complete_operation("op-123", OperationStatus.COMPLETED)

        delegate.complete_operation.assert_awaited_once_with(
            "op-123", OperationStatus.COMPLETED
        )


# ---------------------------------------------------------------------------
# SSEOperationRegistry
# ---------------------------------------------------------------------------


class TestSSEOperationRegistry:
    """Tests registry CRUD operations for operation queues."""

    async def test_register_returns_queue(self):
        registry = SSEOperationRegistry()
        queue = await registry.register("op-1")

        assert isinstance(queue, asyncio.Queue)

    async def test_get_queue_returns_registered_queue(self):
        registry = SSEOperationRegistry()
        original = await registry.register("op-1")
        retrieved = await registry.get_queue("op-1")

        assert retrieved is original

    async def test_get_queue_returns_none_for_unknown(self):
        registry = SSEOperationRegistry()
        result = await registry.get_queue("nonexistent")

        assert result is None

    async def test_unregister_removes_queue(self):
        registry = SSEOperationRegistry()
        await registry.register("op-1")
        await registry.unregister("op-1")

        assert await registry.get_queue("op-1") is None

    async def test_unregister_unknown_is_noop(self):
        registry = SSEOperationRegistry()
        await registry.unregister("nonexistent")  # Should not raise


# ---------------------------------------------------------------------------
# SSEProgressSubscriber
# ---------------------------------------------------------------------------


class TestSSEProgressSubscriber:
    """Tests that the subscriber correctly routes events to SSE queues."""

    async def test_on_operation_started_puts_event(self):
        registry = SSEOperationRegistry()
        queue = await registry.register("op-1")
        subscriber = SSEProgressSubscriber(registry)

        operation = ProgressOperation(
            operation_id="op-1",
            description="Test import",
            total_items=100,
            status=OperationStatus.RUNNING,
        )
        await subscriber.on_operation_started(operation)

        event = queue.get_nowait()
        assert event["event"] == "started"
        assert event["id"] == "evt_1"
        assert event["data"]["operation_id"] == "op-1"
        assert event["data"]["description"] == "Test import"
        assert event["data"]["total"] == 100

    async def test_on_progress_event_puts_event(self):
        registry = SSEOperationRegistry()
        queue = await registry.register("op-1")
        subscriber = SSEProgressSubscriber(registry)

        # Initialize counter
        operation = ProgressOperation(
            operation_id="op-1", status=OperationStatus.RUNNING
        )
        await subscriber.on_operation_started(operation)
        queue.get_nowait()  # Drain the started event

        event = create_progress_event("op-1", current=50, total=100, message="Halfway")
        await subscriber.on_progress_event(event)

        sse_event = queue.get_nowait()
        assert sse_event["event"] == "progress"
        assert sse_event["id"] == "evt_2"
        assert sse_event["data"]["current"] == 50
        assert sse_event["data"]["total"] == 100
        assert sse_event["data"]["message"] == "Halfway"
        assert sse_event["data"]["completion_percentage"] == 50.0

    async def test_completing_top_level_op_emits_nothing(self):
        # The SSE seam (run_sse_operation) owns the terminal event + sentinel for a
        # registered top-level op — it carries the OperationResult counts the
        # subscriber never had. The subscriber must NOT emit them too (double event).
        registry = SSEOperationRegistry()
        queue = await registry.register("op-1")
        subscriber = SSEProgressSubscriber(registry)

        await subscriber.on_operation_completed("op-1", OperationStatus.COMPLETED)
        assert queue.empty()

    async def test_completing_top_level_failed_op_emits_nothing(self):
        registry = SSEOperationRegistry()
        queue = await registry.register("op-1")
        subscriber = SSEProgressSubscriber(registry)

        await subscriber.on_operation_completed("op-1", OperationStatus.FAILED)
        assert queue.empty()

    async def test_ignores_unregistered_operations(self):
        registry = SSEOperationRegistry()
        subscriber = SSEProgressSubscriber(registry)

        # These should not raise even though "unknown" isn't registered
        operation = ProgressOperation(
            operation_id="unknown", status=OperationStatus.RUNNING
        )
        await subscriber.on_operation_started(operation)

        event = create_progress_event("unknown", current=1, total=10, message="Test")
        await subscriber.on_progress_event(event)

        await subscriber.on_operation_completed("unknown", OperationStatus.COMPLETED)

    async def test_event_counter_increments(self):
        registry = SSEOperationRegistry()
        queue = await registry.register("op-1")
        subscriber = SSEProgressSubscriber(registry)

        operation = ProgressOperation(
            operation_id="op-1", status=OperationStatus.RUNNING
        )
        await subscriber.on_operation_started(operation)

        for i in range(3):
            event = create_progress_event(
                "op-1", current=i, total=10, message=f"Step {i}"
            )
            await subscriber.on_progress_event(event)

        # Drain all events and check IDs
        events = []
        while not queue.empty():
            events.append(queue.get_nowait())

        ids = [e["id"] for e in events]
        assert ids == ["evt_1", "evt_2", "evt_3", "evt_4"]  # started + 3 progress


# ---------------------------------------------------------------------------
# SSEProgressSubscriber — Sub-operation routing
# ---------------------------------------------------------------------------


class TestSSEProgressSubscriberSubOperations:
    """Tests that sub-operation events route to parent queues correctly."""

    async def test_sub_operation_started_routes_to_parent_queue(self):
        registry = SSEOperationRegistry()
        parent_queue = await registry.register("op-1")
        subscriber = SSEProgressSubscriber(registry)

        # Start the parent operation first to initialize its counter
        parent_op = ProgressOperation(
            operation_id="op-1",
            description="Workflow run",
            status=OperationStatus.RUNNING,
        )
        await subscriber.on_operation_started(parent_op)
        parent_queue.get_nowait()  # Drain the parent started event

        # Start a sub-operation with parent metadata
        sub_op = ProgressOperation(
            operation_id="sub-1",
            description="Fetching lastfm metadata",
            total_items=50,
            status=OperationStatus.RUNNING,
            metadata={
                "parent_operation_id": "op-1",
                "phase": "enrich",
                "node_type": "enricher",
            },
        )
        await subscriber.on_operation_started(sub_op)

        event = parent_queue.get_nowait()
        assert event["event"] == "sub_operation_started"
        assert event["data"]["operation_id"] == "sub-1"
        assert event["data"]["parent_operation_id"] == "op-1"
        assert event["data"]["description"] == "Fetching lastfm metadata"
        assert event["data"]["total"] == 50
        assert event["data"]["phase"] == "enrich"
        assert event["data"]["node_type"] == "enricher"

    async def test_sub_progress_routes_to_parent_queue(self):
        registry = SSEOperationRegistry()
        parent_queue = await registry.register("op-1")
        subscriber = SSEProgressSubscriber(registry)

        # Start parent
        parent_op = ProgressOperation(
            operation_id="op-1",
            description="Workflow run",
            status=OperationStatus.RUNNING,
        )
        await subscriber.on_operation_started(parent_op)
        parent_queue.get_nowait()  # Drain

        # Start sub-operation
        sub_op = ProgressOperation(
            operation_id="sub-1",
            description="Fetching metadata",
            total_items=100,
            status=OperationStatus.RUNNING,
            metadata={
                "parent_operation_id": "op-1",
                "phase": "enrich",
                "node_type": "enricher",
            },
        )
        await subscriber.on_operation_started(sub_op)
        parent_queue.get_nowait()  # Drain sub_operation_started

        # Emit progress for the sub-operation
        progress_event = create_progress_event(
            "sub-1", current=25, total=100, message="Processed 25/100"
        )
        await subscriber.on_progress_event(progress_event)

        event = parent_queue.get_nowait()
        assert event["event"] == "sub_progress"
        assert event["data"]["operation_id"] == "sub-1"
        assert event["data"]["parent_operation_id"] == "op-1"
        assert event["data"]["current"] == 25
        assert event["data"]["total"] == 100
        assert event["data"]["message"] == "Processed 25/100"
        assert event["data"]["completion_percentage"] == 25.0

    async def test_sub_operation_completed_routes_to_parent(self):
        registry = SSEOperationRegistry()
        parent_queue = await registry.register("op-1")
        subscriber = SSEProgressSubscriber(registry)

        # Start parent
        parent_op = ProgressOperation(
            operation_id="op-1",
            description="Workflow run",
            status=OperationStatus.RUNNING,
        )
        await subscriber.on_operation_started(parent_op)
        parent_queue.get_nowait()  # Drain

        # Start sub-operation
        sub_op = ProgressOperation(
            operation_id="sub-1",
            description="Fetching metadata",
            total_items=100,
            status=OperationStatus.RUNNING,
            metadata={
                "parent_operation_id": "op-1",
                "phase": "enrich",
                "node_type": "enricher",
            },
        )
        await subscriber.on_operation_started(sub_op)
        parent_queue.get_nowait()  # Drain sub_operation_started

        # Complete the sub-operation
        await subscriber.on_operation_completed("sub-1", OperationStatus.COMPLETED)

        event = parent_queue.get_nowait()
        assert event["event"] == "sub_operation_completed"
        assert event["data"]["operation_id"] == "sub-1"
        assert event["data"]["parent_operation_id"] == "op-1"
        assert event["data"]["final_status"] == "completed"

        # No sentinel should be placed (only parent completion sends sentinel)
        assert parent_queue.empty()


# ---------------------------------------------------------------------------
# SSEProgressSubscriber — depth beyond one level
# ---------------------------------------------------------------------------


class TestSSEProgressSubscriberDepth:
    """A generation below a sub-operation must still reach the streams above it.

    Routing used to stop at the immediate parent, so a run nested two deep — a
    queued export file's *resolve* phase, under the file, under the drain —
    reached the file's stream and nowhere else. The drain is exactly the surface
    that wants to say "file 4 of 13 is resolving", so the generation carrying
    that fact was invisible to its only interested reader.
    """

    @staticmethod
    def _child(operation_id: str, parent: str, **metadata: object) -> ProgressOperation:
        return ProgressOperation(
            operation_id=operation_id,
            description=operation_id,
            status=OperationStatus.RUNNING,
            metadata={"parent_operation_id": parent, **metadata},
        )

    @staticmethod
    def _drain(queue) -> list[dict]:
        events = []
        while not queue.empty():
            events.append(queue.get_nowait())
        return events

    async def test_a_registered_child_appears_on_its_own_stream_and_its_parents(self):
        registry = SSEOperationRegistry()
        subscriber = SSEProgressSubscriber(registry)
        drain_queue = await registry.register("drain")
        file_queue = await registry.register("file-1")

        await subscriber.on_operation_started(self._child("file-1", "drain"))

        # Its own stream sees the ordinary top-level `started`...
        assert [e["event"] for e in self._drain(file_queue)] == ["started"]
        # ...and the drain sees the same run as one of its items.
        [announced] = self._drain(drain_queue)
        assert announced["event"] == "sub_operation_started"
        assert announced["data"]["operation_id"] == "file-1"
        assert announced["data"]["item_operation_id"] == "file-1"

    async def test_grandchild_progress_reaches_both_streams_named_correctly(self):
        registry = SSEOperationRegistry()
        subscriber = SSEProgressSubscriber(registry)
        drain_queue = await registry.register("drain")
        file_queue = await registry.register("file-1")

        await subscriber.on_operation_started(self._child("file-1", "drain"))
        await subscriber.on_operation_started(
            self._child("resolve-1", "file-1", phase="match")
        )
        _ = self._drain(drain_queue), self._drain(file_queue)

        await subscriber.on_progress_event(
            create_progress_event("resolve-1", current=812, total=1204, message="…")
        )

        [on_file] = self._drain(file_queue)
        [on_drain] = self._drain(drain_queue)
        assert on_file["event"] == on_drain["event"] == "sub_progress"
        assert on_file["data"]["current"] == on_drain["data"]["current"] == 812

        # The phase names itself as the item on the file's stream — but on the
        # drain's, the item is the FILE. That is what lets one row own the
        # progress without the client rebuilding the tree from parent ids.
        assert on_file["data"]["item_operation_id"] == "resolve-1"
        assert on_drain["data"]["item_operation_id"] == "file-1"

    async def test_an_unregistered_middle_generation_is_walked_through(self):
        # Nothing registers a phase operation's own stream, so the drain can
        # only ever see a phase by walking past it.
        registry = SSEOperationRegistry()
        subscriber = SSEProgressSubscriber(registry)
        drain_queue = await registry.register("drain")

        await subscriber.on_operation_started(self._child("file-1", "drain"))
        await subscriber.on_operation_started(self._child("resolve-1", "file-1"))
        _ = self._drain(drain_queue)

        await subscriber.on_progress_event(
            create_progress_event("resolve-1", current=5, total=10, message="…")
        )

        [event] = self._drain(drain_queue)
        assert event["data"]["operation_id"] == "resolve-1"
        assert event["data"]["item_operation_id"] == "file-1"

    async def test_ids_stay_on_one_monotonic_sequence_per_stream(self):
        # Last-Event-ID resume filters on the number, so two producers writing
        # to one stream must never restart or interleave its ids.
        registry = SSEOperationRegistry()
        subscriber = SSEProgressSubscriber(registry)
        drain_queue = await registry.register("drain")
        await registry.register("file-1")

        await subscriber.on_operation_started(self._child("file-1", "drain"))
        await subscriber.on_operation_started(self._child("resolve-1", "file-1"))
        await subscriber.on_progress_event(
            create_progress_event("resolve-1", current=1, total=2, message="…")
        )
        await subscriber.on_operation_completed("resolve-1", OperationStatus.COMPLETED)

        ids = [e["id"] for e in self._drain(drain_queue)]
        assert ids == ["evt_1", "evt_2", "evt_3", "evt_4"]

    async def test_a_cycle_in_the_chain_does_not_hang_the_walk(self):
        # Parent ids arrive as caller-supplied metadata; a bad one must cost an
        # event, not the event loop.
        registry = SSEOperationRegistry()
        subscriber = SSEProgressSubscriber(registry)
        queue = await registry.register("a")

        await subscriber.on_operation_started(self._child("b", "a"))
        await subscriber.on_operation_started(self._child("a", "b"))

        await subscriber.on_progress_event(
            create_progress_event("b", current=1, total=2, message="…")
        )
        assert not queue.empty()

    async def test_completing_an_unregistered_op_drops_its_edge(self):
        # Nothing unregisters an operation that never had a stream, so without
        # this the tree would grow for the life of the process.
        registry = SSEOperationRegistry()
        subscriber = SSEProgressSubscriber(registry)
        _ = await registry.register("drain")

        await subscriber.on_operation_started(self._child("phase-1", "drain"))
        await subscriber.on_operation_completed("phase-1", OperationStatus.COMPLETED)

        assert await registry.ancestor_streams("phase-1") == []
