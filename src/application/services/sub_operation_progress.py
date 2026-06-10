"""Bridge between infrastructure progress callbacks and AsyncProgressManager sub-operations.

Creates infrastructure-compatible callbacks (plain async callables) that emit
progress events as sub-operations on the application's AsyncProgressManager.
This keeps infrastructure free of application imports while enabling granular
progress tracking for rate-limited batch processing and phased operations.
"""

import asyncio
import time

from attrs import define, field

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


@define(slots=True)
class ThrottledSubOperationEmitter:
    """Throttled progress sink for one sub-operation lifecycle.

    Caps emissions to one every ``min_interval_seconds`` (default 250 ms,
    i.e. 4 Hz). The terminal tick (``completed == total``) always emits
    immediately. If a non-terminal call would otherwise be suppressed, a
    tail-flush timer is scheduled so the most recent ``(completed, total,
    message)`` tuple still emits within ``min_interval_seconds`` — the bar
    never freezes mid-progress.

    The instance is callable, satisfying ``ProgressCallback``. Use
    ``await emitter(completed, total, message)`` from infrastructure code,
    and ``await emitter.aclose(...)`` to mark the sub-op complete (which
    also cancels any pending tail-flush so stale progress never fires
    after teardown).
    """

    sub_op_id: str
    manager: AsyncProgressManager
    min_interval_seconds: float
    last_emit: float = field(default=0.0, init=False)
    last_seen: tuple[int, int, str] | None = field(default=None, init=False)
    pending_tail: asyncio.Task[None] | None = field(default=None, init=False)

    async def _emit(self, completed: int, total: int, message: str) -> None:
        event = create_progress_event(
            operation_id=self.sub_op_id,
            current=completed,
            total=total,
            message=message,
        )
        await self.manager.emit_progress(event)

    async def _flush_after_interval(self) -> None:
        """Sleep the debounce interval, then emit the latest suppressed update.

        Extracted from ``_tail_flush`` so the protective ``try`` clause stays
        small; the same statements remain guarded by the caller's
        ``CancelledError`` handler and ``finally``.
        """
        await asyncio.sleep(self.min_interval_seconds)
        if self.last_seen is None:
            return
        completed, total, message = self.last_seen
        self.last_seen = None
        self.last_emit = time.monotonic()
        await self._emit(completed, total, message)

    async def _tail_flush(self) -> None:
        try:
            await self._flush_after_interval()
        except asyncio.CancelledError:
            # Cancelled by aclose() or by an immediate emit superseding
            # this scheduled tail. No-op.
            pass
        finally:
            self.pending_tail = None

    async def __call__(self, completed: int, total: int, message: str) -> None:
        # Closure-equivalent state mutated synchronously between awaits is
        # safe because asyncio is single-threaded.
        now = time.monotonic()
        is_terminal = total > 0 and completed >= total
        elapsed = now - self.last_emit

        if is_terminal or elapsed >= self.min_interval_seconds:
            # Cancel any pending tail — its data is now stale.
            existing = self.pending_tail
            self.pending_tail = None
            if existing is not None and not existing.done():
                existing.cancel()
            self.last_seen = None
            self.last_emit = now
            await self._emit(completed, total, message)
            return

        # Suppress; capture latest for tail-flush.
        self.last_seen = (completed, total, message)
        if self.pending_tail is None:
            self.pending_tail = asyncio.create_task(self._tail_flush())

    async def aclose(self, status: OperationStatus = OperationStatus.COMPLETED) -> None:
        """Mark the sub-op complete and cancel any pending tail-flush."""
        pending = self.pending_tail
        self.pending_tail = None
        if pending is not None and not pending.done():
            pending.cancel()
        await self.manager.complete_operation(self.sub_op_id, status)


async def create_throttled_sub_operation(
    progress_manager: AsyncProgressManager,
    description: str,
    total_items: int | None,
    parent_operation_id: str,
    phase: Phase,
    node_type: NodeType,
    *,
    min_interval_seconds: float = _DEFAULT_THROTTLE_INTERVAL_SECONDS,
) -> ThrottledSubOperationEmitter:
    """Like ``create_sub_operation``, but throttles emissions.

    The returned emitter is both the infrastructure progress callback
    (callable as ``ProgressCallback``) and the teardown handle —
    ``await emitter.aclose()`` cancels any pending tail-flush along with
    marking the sub-op complete. The sub-operation id is on the instance
    as ``emitter.sub_op_id`` if a caller needs it.

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
    return ThrottledSubOperationEmitter(
        sub_op_id=sub_op_id,
        manager=progress_manager,
        min_interval_seconds=min_interval_seconds,
    )


async def complete_sub_operation(
    progress_manager: AsyncProgressManager,
    sub_operation_id: str,
    status: OperationStatus = OperationStatus.COMPLETED,
) -> None:
    """Complete a sub-operation created via ``create_sub_operation``.

    Throttled sub-operations should be torn down via
    ``ThrottledSubOperationEmitter.aclose()`` instead, since only the
    emitter knows about its pending tail-flush.
    """
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
