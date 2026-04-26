"""Shared SSE operation setup and terminal event construction.

Eliminates duplication across import, playlist, and workflow route handlers.
Reusable primitives:

- ``prepare_sse_operation`` — minimal setup (uuid + queue registration).
- ``prepare_sse_operation_with_emitter`` — full kickoff: 429 concurrency
  check, queue registration, an ``OperationBoundEmitter`` wired to the
  global progress manager, and an ``OperationRun`` audit-log row written
  via :mod:`application.services.operation_run_recorder`.
- ``run_sse_operation`` — background-task wrapper that owns the cleanup
  (fallback error event on uncaught exception, sentinel, grace period,
  unregister) and finalizes the ``OperationRun`` row on terminal events.
  Pair with ``launch_background`` in the route handler.
- ``launch_sse_operation`` — one-shot helper combining all of the above
  for routes that follow the standard kickoff → background → return-202
  shape. Pass an emitter-taking coroutine factory; the helper handles
  the rest.
"""

import asyncio
from collections.abc import Awaitable, Callable
from uuid import UUID, uuid4

from fastapi import HTTPException

from src.application.services.operation_run_recorder import (
    finalize_run,
    start_run,
)
from src.application.services.progress_manager import get_progress_manager
from src.config import get_logger
from src.config.constants import SSEConstants
from src.interface.api.schemas.imports import OperationStartedResponse
from src.interface.api.services.background import (
    finalize_sse_operation,
    launch_background,
)
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


async def prepare_sse_operation_with_emitter(
    *,
    user_id: str,
    operation_type: str,
) -> tuple[str, UUID, OperationBoundEmitter]:
    """Pre-generate operation_id, register SSE queue, build a bound emitter,
    and write the ``OperationRun`` audit row at kickoff.

    Raises ``HTTPException(429)`` if the concurrent operation limit is
    reached, checked *before* allocating any resources so the registry
    never accumulates orphan queues or audit rows on rejection.

    Returns ``(operation_id, run_id, emitter)``. The run_id should be
    threaded through to ``run_sse_operation`` so the row gets finalized
    on terminal events.
    """
    if len(_active_operations) >= SSEConstants.MAX_CONCURRENT_OPERATIONS:
        raise HTTPException(
            status_code=429,
            detail="Too many concurrent operations. Please wait for a running operation to finish.",
            headers={"Retry-After": str(SSEConstants.GRACE_PERIOD_SECONDS)},
        )
    # Write the audit row before allocating the SSE queue so a failed
    # audit-write doesn't leave an orphan queue in the registry. The
    # 429 check above guarantees we never write a row we can't service.
    run_id = await start_run(user_id=user_id, operation_type=operation_type)
    operation_id, _ = await prepare_sse_operation()
    emitter = OperationBoundEmitter(
        delegate=get_progress_manager(), operation_id=operation_id
    )
    return operation_id, run_id, emitter


async def run_sse_operation(
    operation_id: str,
    coro: Awaitable[object],
    *,
    run_id: UUID | None = None,
    user_id: str | None = None,
) -> None:
    """Run a use-case coroutine with full SSE lifecycle cleanup.

    On uncaught exception, pushes a fallback ``error`` event + sentinel so
    the SSE generator closes cleanly instead of hanging on ``queue.get()``.
    Always marks the operation inactive before the grace period so new
    kickoffs aren't blocked by draining tasks, then runs the shared
    sentinel + grace period + unregister cleanup.

    When ``run_id`` and ``user_id`` are provided (paired — both or
    neither), finalizes the matching ``OperationRun`` row on success
    (``status="complete"``) or exception (``status="error"``). The
    finalize call is best-effort: a failed audit-write is logged but does
    not propagate, since the user-visible work has already succeeded.
    """
    registry = get_operation_registry()
    _active_operations.add(operation_id)
    try:
        await coro
        if run_id is not None and user_id is not None:
            try:
                await finalize_run(run_id, user_id=user_id, status="complete")
            except Exception:
                logger.error(
                    "Failed to finalize OperationRun row on completion",
                    operation_id=operation_id,
                    run_id=str(run_id),
                    exc_info=True,
                )
    except Exception as exc:
        logger.error("SSE operation failed", operation_id=operation_id, exc_info=True)
        if run_id is not None and user_id is not None:
            try:
                await finalize_run(
                    run_id,
                    user_id=user_id,
                    status="error",
                    counts={"error_message": str(exc)[:500]},
                )
            except Exception:
                logger.error(
                    "Failed to finalize OperationRun row on error",
                    operation_id=operation_id,
                    run_id=str(run_id),
                    exc_info=True,
                )
        queue = await registry.get_queue(operation_id)
        if queue is not None and queue.empty():
            await queue.put({
                "event": "error",
                "data": {
                    "operation_id": operation_id,
                    "final_status": "failed",
                    "message": "Operation failed unexpectedly",
                    **({"run_id": str(run_id)} if run_id is not None else {}),
                },
            })
            await queue.put(SSE_SENTINEL)
    finally:
        _active_operations.discard(operation_id)
        await finalize_sse_operation(operation_id)


async def launch_sse_operation(
    *,
    user_id: str,
    operation_type: str,
    coro_factory: Callable[[OperationBoundEmitter], Awaitable[None]],
    name_prefix: str = "import",
) -> OperationStartedResponse:
    """Run the standard kickoff → background → return-202 shape.

    Six routes share this exact pattern (Last.fm/Spotify imports, likes
    sync/export, connector playlist import, bulk apply-assignments).
    Wrapping it here keeps each route handler at ~3 lines of business
    logic — define the use case call, pass the factory, return.
    """
    operation_id, run_id, emitter = await prepare_sse_operation_with_emitter(
        user_id=user_id, operation_type=operation_type
    )
    # Create the coroutine inside the lambda so a stubbed/no-op
    # ``launch_background`` (e.g., in tests) doesn't leave an unawaited
    # coroutine warning when the factory is never invoked.
    launch_background(
        f"{name_prefix}_{operation_id}",
        lambda: run_sse_operation(
            operation_id,
            coro_factory(emitter),
            run_id=run_id,
            user_id=user_id,
        ),
    )
    return OperationStartedResponse(operation_id=operation_id, run_id=str(run_id))


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
