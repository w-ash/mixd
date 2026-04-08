"""Background task launcher and SSE lifecycle helpers for API route handlers.

Shared by import and workflow endpoints:
- ``launch_background`` wraps asyncio.create_task with strong-reference tracking.
- ``finalize_sse_operation`` handles the sentinel + grace period + unregister
  pattern that both import and workflow background tasks share.
"""

# Legitimate Any: Coroutine type params are inherently Any

import asyncio
from collections.abc import Callable, Coroutine
import time
from typing import Any
from uuid import UUID

from attrs import define

from src.config import get_logger
from src.config.constants import SSEConstants

logger = get_logger(__name__).bind(service="background_tasks")

# Strong references prevent background tasks from being garbage-collected
_background_tasks: set[asyncio.Task[None]] = set()


@define(frozen=True, slots=True)
class _TaskMeta:
    workflow_id: str
    run_id: UUID
    started_at_ns: int


# Task metadata for enriched done-callback logging
_task_meta: dict[str, _TaskMeta] = {}


def _on_task_done(task: asyncio.Task[None]) -> None:
    """Log background task outcome with duration and workflow context."""
    _background_tasks.discard(task)
    name = task.get_name()

    # Extract and clean up metadata
    meta = _task_meta.pop(name, None)
    extra: dict[str, Any] = {"task_name": name}
    if meta is not None:
        extra["workflow_id"] = meta.workflow_id
        extra["run_id"] = meta.run_id
        extra["duration_ms"] = (
            time.perf_counter_ns() - meta.started_at_ns
        ) // 1_000_000

    if task.cancelled():
        logger.warning("Background task cancelled", **extra)
    elif exc := task.exception():
        logger.error("Background task failed", exc_info=exc, **extra)
    else:
        logger.info("Background task completed", **extra)


def launch_background(
    name: str,
    coro_factory: Callable[[], Coroutine[Any, Any, None]],
    *,
    workflow_id: str | None = None,
    run_id: UUID | None = None,
) -> None:
    """Launch a background coroutine and prevent GC of the task handle.

    Accepts a *factory* (zero-arg callable returning a coroutine) rather than
    a pre-created coroutine so tests can stub this without leaking unawaited
    coroutine objects. Optional ``workflow_id``/``run_id`` are stored for
    enriched done-callback logging.
    """
    task = asyncio.create_task(coro_factory(), name=name)
    _background_tasks.add(task)
    task.add_done_callback(_on_task_done)
    # Store metadata AFTER task creation + callback registration so that
    # a failed create_task() doesn't leave an orphan entry in _task_meta.
    if workflow_id is not None and run_id is not None:
        _task_meta[name] = _TaskMeta(
            workflow_id=workflow_id,
            run_id=run_id,
            started_at_ns=time.perf_counter_ns(),
        )


async def finalize_sse_operation(operation_id: str) -> None:
    """Send SSE sentinel and clean up the operation registry after a grace period.

    Shared cleanup pattern for both import and workflow background tasks:
    1. Push SSE_SENTINEL to tell the SSE generator to close the connection
    2. Wait a grace period so SSE clients can read final events
    3. Unregister the operation from the registry

    This function is safe to call even if the queue has already been
    unregistered (e.g. on cancellation).
    """
    from src.interface.api.services.progress import SSE_SENTINEL, get_operation_registry

    registry = get_operation_registry()
    queue = await registry.get_queue(operation_id)
    if queue is not None:
        await queue.put(SSE_SENTINEL)
    await asyncio.sleep(SSEConstants.GRACE_PERIOD_SECONDS)
    await registry.unregister(operation_id)
