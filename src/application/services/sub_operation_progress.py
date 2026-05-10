"""Bridge between infrastructure progress callbacks and AsyncProgressManager sub-operations.

Creates infrastructure-compatible callbacks (plain async callables) that emit
progress events as sub-operations on the application's AsyncProgressManager.
This keeps infrastructure free of application imports while enabling granular
progress tracking for rate-limited batch processing and phased operations.
"""

import asyncio
import time

from src.config import get_logger
from src.config.constants import NodeType, Phase
from src.domain.entities.progress import (
    OperationStatus,
    ProgressOperation,
    create_progress_event,
)
from src.domain.matching.types import ProgressCallback

from .progress_manager import AsyncProgressManager

logger = get_logger(__name__).bind(service="sub_operation_progress")

# 4 Hz default — caps SSE wire rate without freezing the bar visibly.
_DEFAULT_THROTTLE_INTERVAL_SECONDS = 0.25

# Module-level registry of pending tail-flush timers per sub_operation_id.
# complete_sub_operation cancels any pending tail before completing so
# stale progress doesn't fire after the op is marked done.
_pending_tails: dict[str, asyncio.Task[None]] = {}


async def create_sub_operation(
    progress_manager: AsyncProgressManager,
    description: str,
    total_items: int | None,
    parent_operation_id: str,
    phase: Phase,
    node_type: NodeType,
) -> tuple[str, ProgressCallback]:
    """Create a sub-operation and return an infrastructure-compatible callback.

    Starts a ProgressOperation on the manager with parent metadata,
    then returns a callback that callers can invoke with (completed, total, message)
    to emit progress events.

    Args:
        progress_manager: The application progress manager.
        description: Human-readable sub-operation description.
        total_items: Expected total (None for indeterminate).
        parent_operation_id: ID of the parent workflow operation.
        phase: Phase identifier (e.g., "fetch", "enrich", "save").
        node_type: Node type for context (e.g., "enricher", "source").

    Returns:
        Tuple of (sub_operation_id, callback_fn).
    """
    operation = ProgressOperation(
        description=description,
        total_items=total_items,
        metadata={
            "parent_operation_id": parent_operation_id,
            "phase": phase,
            "node_type": node_type,
        },
    )

    sub_op_id = await progress_manager.start_operation(operation)

    async def callback(completed: int, total: int, message: str) -> None:
        event = create_progress_event(
            operation_id=sub_op_id,
            current=completed,
            total=total,
            message=message,
        )
        await progress_manager.emit_progress(event)

    return sub_op_id, callback


async def create_throttled_sub_operation(
    progress_manager: AsyncProgressManager,
    description: str,
    total_items: int | None,
    parent_operation_id: str,
    phase: Phase,
    node_type: NodeType,
    *,
    min_interval_seconds: float = _DEFAULT_THROTTLE_INTERVAL_SECONDS,
) -> tuple[str, ProgressCallback]:
    """Like ``create_sub_operation``, but throttles emissions.

    Caps emissions to one every ``min_interval_seconds`` (default 250 ms,
    i.e. 4 Hz). The terminal tick (``completed == total``) always emits
    immediately. If a non-terminal call would otherwise be suppressed, a
    tail-flush timer is scheduled so the most recent ``(completed, total,
    message)`` tuple still emits within ``min_interval_seconds`` — the bar
    never freezes mid-progress.

    Use the same ``complete_sub_operation`` for teardown; it cancels any
    pending tail-flush timer for this sub-op so stale progress doesn't fire
    after the op is marked complete.

    Why throttle here and not at ``AsyncProgressManager``: the manager also
    feeds the CLI's ``RichProgressProvider`` and ``OperationRunRecorder``,
    which want full-fidelity events. The SSE wire is the only consumer with
    a re-render cost, and per-item progress callbacks are the only producer
    that can flood it. This is the right seam.
    """
    operation = ProgressOperation(
        description=description,
        total_items=total_items,
        metadata={
            "parent_operation_id": parent_operation_id,
            "phase": phase,
            "node_type": node_type,
        },
    )

    sub_op_id = await progress_manager.start_operation(operation)

    # Closure state mutated by the returned callback. Safe without a lock
    # because asyncio is single-threaded: the synchronous statements
    # between awaits run atomically, so concurrent callbacks (e.g. from a
    # TaskGroup) can't observe a partially-updated last_emit/last_seen.
    last_emit = 0.0
    last_seen: tuple[int, int, str] | None = None

    async def emit(completed: int, total: int, message: str) -> None:
        event = create_progress_event(
            operation_id=sub_op_id,
            current=completed,
            total=total,
            message=message,
        )
        await progress_manager.emit_progress(event)

    async def tail_flush() -> None:
        nonlocal last_emit, last_seen
        try:
            await asyncio.sleep(min_interval_seconds)
            if last_seen is None:
                return
            completed, total, message = last_seen
            last_seen = None
            last_emit = time.monotonic()
            await emit(completed, total, message)
        except asyncio.CancelledError:
            # Cancelled by complete_sub_operation or by an immediate emit
            # superseding this scheduled tail. No-op.
            pass
        finally:
            _pending_tails.pop(sub_op_id, None)

    async def callback(completed: int, total: int, message: str) -> None:
        nonlocal last_emit, last_seen
        now = time.monotonic()
        is_terminal = total > 0 and completed >= total
        elapsed = now - last_emit

        if is_terminal or elapsed >= min_interval_seconds:
            # Cancel any pending tail — its data is now stale.
            existing = _pending_tails.pop(sub_op_id, None)
            if existing is not None and not existing.done():
                existing.cancel()
            last_seen = None
            last_emit = now
            await emit(completed, total, message)
            return

        # Suppress; capture latest for tail-flush.
        last_seen = (completed, total, message)
        if sub_op_id not in _pending_tails:
            _pending_tails[sub_op_id] = asyncio.create_task(tail_flush())

    return sub_op_id, callback


async def complete_sub_operation(
    progress_manager: AsyncProgressManager,
    sub_operation_id: str,
    status: OperationStatus = OperationStatus.COMPLETED,
) -> None:
    """Complete a sub-operation with the given status.

    Cancels any pending throttle tail-flush timer for this sub-op so
    progress events don't fire after completion.
    """
    pending = _pending_tails.pop(sub_operation_id, None)
    if pending is not None and not pending.done():
        pending.cancel()
    await progress_manager.complete_operation(sub_operation_id, status)


async def emit_phase_progress(
    progress_manager: AsyncProgressManager,
    parent_operation_id: str,
    phase: Phase,
    node_type: NodeType,
    message: str,
) -> None:
    """Emit a lightweight phase transition for source/destination nodes.

    Creates a short-lived indeterminate sub-operation that signals a phase
    change (e.g., "Fetching playlist from Spotify") without item-level counting.
    The sub-operation completes immediately after creation.

    Args:
        progress_manager: The application progress manager.
        parent_operation_id: ID of the parent workflow operation.
        phase: Phase identifier (e.g., "fetch", "save", "sync").
        node_type: Node type (e.g., "source", "destination").
        message: Human-readable phase description.
    """
    operation = ProgressOperation(
        description=message,
        total_items=None,
        metadata={
            "parent_operation_id": parent_operation_id,
            "phase": phase,
            "node_type": node_type,
        },
    )

    sub_op_id = await progress_manager.start_operation(operation)

    # Emit a single progress event for the phase
    event = create_progress_event(
        operation_id=sub_op_id,
        current=0,
        total=None,
        message=message,
    )
    await progress_manager.emit_progress(event)

    # Phase sub-operations complete immediately — they're just signals
    await progress_manager.complete_operation(sub_op_id, OperationStatus.COMPLETED)
