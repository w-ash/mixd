"""Shared SSE operation setup and terminal event construction.

Eliminates duplication across import, playlist, and workflow route handlers.
Three levels of reusable primitives:

- ``prepare_sse_operation`` — minimal setup (uuid + queue registration).
- ``prepare_sse_operation_with_emitter`` — full kickoff: 429 concurrency
  check, queue registration, and an ``OperationBoundEmitter`` wired to the
  global progress manager. Used by every route that kicks off an async
  background operation and wants structured progress events.
- ``run_sse_operation`` — background-task wrapper that owns the cleanup
  (fallback error event on uncaught exception, sentinel, grace period,
  unregister). Pair with ``launch_background`` in the route handler.
"""

import asyncio
from collections.abc import Awaitable
from uuid import UUID, uuid4

from fastapi import HTTPException

from src.application.services.progress_manager import get_progress_manager
from src.config import get_logger
from src.config.constants import SSEConstants
from src.interface.api.services.background import finalize_sse_operation
from src.interface.api.services.progress import (
    SSE_SENTINEL,
    OperationBoundEmitter,
    get_operation_registry,
)

logger = get_logger(__name__).bind(service="sse_operations")

# Module-level registry of logically active operations. Shared across every
# route that kicks off a background SSE operation so the 429 cap applies
# globally, not per-route. Cleared before the SSE grace period so finished-
# but-draining tasks don't block new kickoffs.
_active_operations: set[str] = set()


async def prepare_sse_operation() -> tuple[str, asyncio.Queue[object]]:
    """Generate an operation_id, register an SSE queue, and return both.

    This is the minimal shared setup. Route-specific guards (e.g. the 429
    concurrency limit in imports) wrap this function rather than replacing it.
    """
    operation_id = str(uuid4())
    registry = get_operation_registry()
    sse_queue = await registry.register(operation_id)
    return operation_id, sse_queue


async def prepare_sse_operation_with_emitter() -> tuple[str, OperationBoundEmitter]:
    """Pre-generate operation_id, register SSE queue, build a bound emitter.

    Raises ``HTTPException(429)`` if the concurrent operation limit is
    reached, checked *before* allocating any resources so the registry
    never accumulates orphan queues on rejection.

    Shared by every route that kicks off a background SSE operation.
    """
    if len(_active_operations) >= SSEConstants.MAX_CONCURRENT_OPERATIONS:
        raise HTTPException(
            status_code=429,
            detail="Too many concurrent operations. Please wait for a running operation to finish.",
            headers={"Retry-After": str(SSEConstants.GRACE_PERIOD_SECONDS)},
        )
    operation_id, _ = await prepare_sse_operation()
    emitter = OperationBoundEmitter(
        delegate=get_progress_manager(), operation_id=operation_id
    )
    return operation_id, emitter


async def run_sse_operation(
    operation_id: str,
    coro: Awaitable[object],
) -> None:
    """Run a use-case coroutine with full SSE lifecycle cleanup.

    On uncaught exception, pushes a fallback ``error`` event + sentinel so
    the SSE generator closes cleanly instead of hanging on ``queue.get()``.
    Always marks the operation inactive before the grace period so new
    kickoffs aren't blocked by draining tasks, then runs the shared
    sentinel + grace period + unregister cleanup.
    """
    registry = get_operation_registry()
    _active_operations.add(operation_id)
    try:
        await coro
    except Exception:
        logger.error("SSE operation failed", operation_id=operation_id, exc_info=True)
        queue = await registry.get_queue(operation_id)
        if queue is not None and queue.empty():
            await queue.put({
                "event": "error",
                "data": {
                    "operation_id": operation_id,
                    "final_status": "failed",
                    "message": "Operation failed unexpectedly",
                },
            })
            await queue.put(SSE_SENTINEL)
    finally:
        _active_operations.discard(operation_id)
        await finalize_sse_operation(operation_id)


def build_terminal_event(
    event_id: str,
    event_type: str,
    operation_id: str,
    status: str,
    *,
    run_id: UUID | None = None,
    **extra: object,
) -> dict[str, object]:
    """Build a terminal SSE event dict with shared structure.

    Used by playlist sync (complete/error), workflow runs, and workflow
    previews to construct the final event pushed to the SSE queue.
    """
    data: dict[str, object] = {
        "operation_id": operation_id,
        "final_status": status,
        **extra,
    }
    if run_id is not None:
        data["run_id"] = run_id
    return {
        "id": event_id,
        "event": event_type,
        "data": data,
    }
