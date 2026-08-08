"""Background task launcher and SSE lifecycle helpers for API route handlers.

Shared by import and workflow endpoints:
- ``launch_background`` wraps asyncio.create_task with strong-reference tracking.
- ``cancel_all_background_tasks`` drains the registry on server shutdown so each
  task takes its cancellation branch while the loop and DB engine are still up.
- ``finalize_sse_operation`` handles the sentinel + grace period + unregister
  pattern that both import and workflow background tasks share.
"""

import asyncio
from collections.abc import Callable, Coroutine
import time
from typing import Final
from uuid import UUID

from attrs import define

from src.config import get_logger
from src.config.constants import SSEConstants

logger = get_logger(__name__).bind(service="background_tasks")

# Strong references prevent background tasks from being garbage-collected
_background_tasks: set[asyncio.Task[None]] = set()

# How long the lifespan shutdown waits for cancelled background tasks to finish
# their terminal bookkeeping. Bounded by Fly's `kill_timeout = 5s`: past that the
# platform SIGKILLs us, so the drain must leave headroom for the rest of the
# lifespan teardown (progress unsubscribe, adapter close). Stragglers are logged
# and abandoned — the process is going away either way.
SHUTDOWN_DRAIN_TIMEOUT_SECONDS: Final = 3.0


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
    extra: dict[str, object] = {"task_name": name}
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
    coro_factory: Callable[[], Coroutine[object, object, None]],
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


async def cancel_all_background_tasks(
    timeout_seconds: float = SHUTDOWN_DRAIN_TIMEOUT_SECONDS,
) -> int:
    """Cancel every registered background task and await them, bounded by the budget.

    Called from the API lifespan's shutdown path so an in-flight SSE operation
    takes its ``CancelledError`` branch — recording the run as failed — *while the
    event loop and DB engine are still healthy*. Without an explicit drain the
    cancellation only arrives during interpreter teardown, where the terminal
    audit write has nowhere to go: the incident where a SIGINT'd (Fly autostop)
    import stayed durably recorded as ``complete`` with empty counts.

    Returns the number of tasks that were still running when the drain began.
    Never raises: a task that fails or refuses to stop is logged, not propagated,
    because shutdown must proceed regardless.
    """
    # Snapshot first — `_on_task_done` mutates `_background_tasks` as tasks settle.
    tasks = [task for task in _background_tasks if not task.done()]
    if not tasks:
        return 0

    logger.info(
        "Cancelling in-flight background tasks",
        task_count=len(tasks),
        timeout_seconds=timeout_seconds,
    )
    for task in tasks:
        task.cancel()

    # asyncio.wait never re-raises the awaited tasks' exceptions (including the
    # CancelledError we just caused), so the drain can't abort the shutdown. It is
    # also the reason this isn't an `asyncio.timeout` scope: a straggler must be
    # abandoned, not cancelled again mid-audit-write.
    _, pending = await asyncio.wait(tasks, timeout=timeout_seconds)
    if pending:
        logger.warning(
            "Background tasks did not settle within the shutdown budget",
            straggler_count=len(pending),
            straggler_names=sorted(task.get_name() for task in pending),
            timeout_seconds=timeout_seconds,
        )
    return len(tasks)


async def finalize_sse_operation(
    operation_id: str, *, grace_period_seconds: float | None = None
) -> None:
    """Send SSE sentinel and clean up the operation registry after a grace period.

    Shared cleanup pattern for both import and workflow background tasks:
    1. Push SSE_SENTINEL to tell the SSE generator to close the connection
    2. Wait a grace period so SSE clients can read final events
    3. Unregister the operation from the registry

    ``grace_period_seconds`` overrides step 2 (default:
    ``SSEConstants.GRACE_PERIOD_SECONDS``). A cancelled operation passes ~0: the
    read window exists for a *live* client, and holding a shutdown for 30s per
    task would blow the kill_timeout budget and strand the drain.

    This function is safe to call even if the queue has already been
    unregistered (e.g. on cancellation).
    """
    from src.interface.api.services.progress import SSE_SENTINEL, get_operation_registry

    registry = get_operation_registry()
    queue = await registry.get_queue(operation_id)
    if queue is not None:
        await queue.put(SSE_SENTINEL)
    if grace_period_seconds is None:
        grace_period_seconds = SSEConstants.GRACE_PERIOD_SECONDS
    await asyncio.sleep(grace_period_seconds)
    await registry.unregister(operation_id)
