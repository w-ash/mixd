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
from asyncio import CancelledError
from collections.abc import Awaitable, Callable
import contextlib
from typing import Final
from uuid import UUID, uuid4

from fastapi import HTTPException

from src.application.services.operation_outcome import audit_outcome, failure_issues
from src.application.services.operation_run_recorder import (
    finalize_run,
    start_run,
)
from src.application.services.progress_broker import get_progress_broker
from src.config import get_logger
from src.config.constants import SSEConstants, WorkflowConstants
from src.domain.entities.operation_run import FAILED_STATUSES, OperationStatus
from src.domain.entities.operations import OperationResult
from src.domain.entities.progress import (
    OperationStatus as ProgressOpStatus,
    ProgressOperation,
)
from src.domain.entities.shared import JsonDict
from src.interface.api.schemas.imports import OperationStartedResponse
from src.interface.api.services.background import (
    finalize_sse_operation,
    launch_background,
)
from src.interface.api.services.progress import (
    OperationBoundEmitter,
    get_operation_registry,
)

logger = get_logger(__name__).bind(service="sse_operations")

# Module-level registry of logically active operations. Shared across every
# route that kicks off a background SSE operation so the 429 cap applies
# globally, not per-route. Cleared before the SSE grace period so finished-
# but-draining tasks don't block new kickoffs.
_active_operations: set[str] = set()

# What a run killed by a server shutdown/restart records. Two strings because they
# land in two places: the terse one in ``counts["error_message"]`` (and so in the
# live toast), the explanatory one in the audit row's issues, where the run log
# has room to say what to do next. Re-running an import is always safe — the
# ingest path is idempotent — so the advice is unconditional.
CANCELLED_ERROR_MESSAGE: Final = "cancelled by server shutdown"
CANCELLED_ISSUE_MESSAGE: Final = (
    "Cancelled by server (shutdown or restart) — the import did not finish; "
    "re-run it (re-imports are idempotent)"
)

# Bounded second wait for a shielded audit write that was still in flight when the
# cancellation arrived. Sized well inside ``SHUTDOWN_DRAIN_TIMEOUT_SECONDS`` so a
# slow write can't consume the whole drain budget on its own.
_AUDIT_WRITE_GRACE_SECONDS: Final = 2.0

# A cancelled operation skips the SSE read window: see ``finalize_sse_operation``.
_CANCELLED_GRACE_PERIOD_SECONDS: Final = 0.0


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
    request_params: JsonDict | None = None,
    initiated_by: str = "manual",
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
    # Mint the operation_id first (without registering the queue), write the
    # audit row WITH it, then register the queue. This preserves the "audit row
    # before queue" guarantee (a failed audit-write leaves no orphan queue)
    # while persisting operation_id so snapshot / active-operations endpoints can
    # resolve the row and a re-attaching client can stream from the same id.
    operation_id = str(uuid4())
    run_id = await start_run(
        user_id=user_id,
        operation_type=operation_type,
        operation_id=operation_id,
        request_params=request_params,
        initiated_by=initiated_by,
    )
    await get_operation_registry().register(operation_id)
    emitter = OperationBoundEmitter(
        delegate=get_progress_broker(), operation_id=operation_id, run_id=run_id
    )
    return operation_id, run_id, emitter


async def run_sse_operation(
    operation_id: str,
    coro: Awaitable[object],
    *,
    run_id: UUID | None = None,
    user_id: str | None = None,
    description: str = "Operation",
) -> None:
    """Run a use-case coroutine with full SSE lifecycle cleanup.

    On uncaught exception, pushes a fallback ``error`` event + sentinel so
    the SSE generator closes cleanly instead of hanging on ``queue.get()``.
    Always marks the operation inactive before the grace period so new
    kickoffs aren't blocked by draining tasks, then runs the shared
    sentinel + grace period + unregister cleanup.

    When ``run_id`` and ``user_id`` are provided (paired — both or
    neither), finalizes the matching ``OperationRun`` row AND emits the live
    terminal SSE event. Both are read from the use case's returned
    ``OperationResult`` via ``audit_outcome``: a handled soft failure
    (``is_failure``) is reported as ``error`` with the run's counts, a clean run
    as ``complete`` with counts, and an uncaught exception as ``error``.

    The terminal event ownership lives here, not in ``SSEProgressSubscriber``:
    the use case's own operations are now *children* of this request (they carry
    ``parent_operation_id``) and only emit ``sub_*`` events, so the subscriber
    never fires the registered-op ``complete``/``error``. This is what gives the
    *live* toast its terminal status + counts (the audit row got them from 1a) — the v0.8.5
    "if a run fails, they see it" fix, mirroring the workflow/preview path that
    has always pushed its own ``build_terminal_event``. ``finalize_sse_operation``
    pushes the single sentinel. Audit-finalize is best-effort.

    A cancellation (server shutdown/restart) is recorded as ``error`` with the
    ``CANCELLED_*`` messages and then re-raised, so the task ends cancelled. The
    audit write is shielded — see ``_finalize_run_shielded``.
    """
    _active_operations.add(operation_id)
    # Own the request operation: it is the top-level op the SSE client is attached
    # to, so the `started` event fires before the use case runs and the use case's
    # own operations route as its children (sub_* events). Best-effort — progress
    # tracking must never break the operation it observes.
    await _safe_start_parent(operation_id, description)
    status: OperationStatus = "complete"
    counts: JsonDict | None = None
    issues: list[JsonDict] = []
    cancellation: CancelledError | None = None
    try:
        result = await coro
    except CancelledError as exc:
        # MUST precede `except Exception`: CancelledError is a BaseException, so the
        # generic handler never sees it. Without this branch a cancellation (Fly
        # autostop → SIGINT → task.cancel()) fell straight through to `finally` with
        # the optimistic `status = "complete"` default still in place — 15,317
        # ingested plays durably recorded as a successful import with empty counts.
        # Mirrors workflow_execution.execute_workflow_background, which has always
        # caught CancelledError, recorded a terminal state, and re-raised.
        cancellation = exc
        logger.warning("SSE operation cancelled", operation_id=operation_id)
        status, counts = "error", {"error_message": CANCELLED_ERROR_MESSAGE}
        issues = [{"message": CANCELLED_ISSUE_MESSAGE}]
    except Exception as exc:
        logger.error("SSE operation failed", operation_id=operation_id, exc_info=True)
        # str(exc) is empty for argument-less exceptions (e.g. `raise KeyError()`);
        # the type name is the only reason left, and a blank issue is worse than none.
        error_message = str(exc)[:500] or type(exc).__name__
        status, counts = "error", {"error_message": error_message}
        issues = [{"message": error_message}]
    else:
        status, counts = audit_outcome(result)
        if status in FAILED_STATUSES and isinstance(result, OperationResult):
            issues = failure_issues(result)
    finally:
        if run_id is not None and user_id is not None:
            await _finalize_run_shielded(
                run_id,
                operation_id,
                user_id=user_id,
                status=status,
                counts=counts,
                issues=issues,
            )
        await _push_terminal_event(operation_id, status, counts, run_id)
        await _safe_complete_parent(operation_id, status)
        _active_operations.discard(operation_id)
        await finalize_sse_operation(
            operation_id,
            grace_period_seconds=(
                _CANCELLED_GRACE_PERIOD_SECONDS if cancellation is not None else None
            ),
        )
    if cancellation is not None:
        # Re-raise once the cleanup above is durable, so the task still ends
        # *cancelled*: `_on_task_done` logs it as such and the shutdown drain sees a
        # settled task. Swallowing it would turn a killed run into a normal return.
        raise cancellation


async def _finalize_run_shielded(
    run_id: UUID,
    operation_id: str,
    *,
    user_id: str,
    status: OperationStatus,
    counts: JsonDict | None,
    issues: list[JsonDict],
) -> None:
    """Write the terminal audit row so it survives cancellation of this task.

    ``asyncio.shield`` is load-bearing, not defensive. This runs from a ``finally``
    that can be entered while a cancellation is being delivered, and an ``await``
    inside such a ``finally`` is re-cancelled at its first suspension point —
    ``finalize_run`` does real DB I/O, so unshielded the UPDATE would be interrupted
    before it lands and the run row would stay exactly as the incident found it. The
    shielded write runs as its own task and completes even as this one dies.

    Status + issues ride ONE ``finalize_run`` call: a separate append could fail
    after the status write and leave a durable failed run with no reason attached.

    Never raises — the terminal SSE event and registry teardown still have to run.
    """
    write = asyncio.ensure_future(
        finalize_run(
            run_id,
            user_id=user_id,
            status=status,
            counts=counts,
            issues=issues,
        )
    )
    try:
        await asyncio.shield(write)
    except CancelledError:
        # Cancelled while the shielded write was in flight. The write itself lives
        # on (that is what the shield buys); wait a bounded moment for it to land so
        # the row is durable before the process goes away. Swallowing the
        # cancellation here doesn't change the task's outcome — `run_sse_operation`
        # re-raises it after the cleanup completes.
        with contextlib.suppress(CancelledError, Exception):
            await asyncio.wait_for(asyncio.shield(write), _AUDIT_WRITE_GRACE_SECONDS)
    except Exception:
        logger.error(
            "Failed to finalize OperationRun row",
            operation_id=operation_id,
            run_id=str(run_id),
            status=status,
            exc_info=True,
        )


async def _safe_start_parent(operation_id: str, description: str) -> None:
    """Start the request (parent) operation; swallow any tracking error."""
    try:
        await get_progress_broker().start_operation(
            ProgressOperation(operation_id=operation_id, description=description)
        )
    except Exception:
        logger.warning(
            "Failed to start parent operation (continuing)",
            operation_id=operation_id,
            exc_info=True,
        )


async def _safe_complete_parent(operation_id: str, status: OperationStatus) -> None:
    """Complete the request op so the coordinator evicts it; swallow tracking errors.

    The live terminal SSE event is already pushed by ``_push_terminal_event`` — the
    subscriber no longer emits it for a registered op — so this call exists purely
    to drive coordinator eviction and a clean lifecycle log.
    """
    final = ProgressOpStatus.FAILED if status == "error" else ProgressOpStatus.COMPLETED
    try:
        await get_progress_broker().complete_operation(operation_id, final)
    except Exception:
        logger.warning(
            "Failed to complete parent operation (continuing)",
            operation_id=operation_id,
            exc_info=True,
        )


async def _push_terminal_event(
    operation_id: str,
    status: OperationStatus,
    counts: JsonDict | None,
    run_id: UUID | None,
) -> None:
    """Push the live terminal SSE event with the run's final status + counts.

    ``error`` → ``error`` event; every other terminal status → ``complete``.
    ``partial`` lands on ``complete`` deliberately: the operation *did* finish, and
    the ``errors`` count rides along in ``counts`` so the live toast still shows
    what went wrong. Only the durable audit row draws the finer distinction — the
    SSE terminal vocabulary is shared with the workflow/preview streams and is not
    the place to introduce a third outcome. ``counts`` are spread into the event
    data so the toast can render the real per-operation numbers (``track_plays``,
    ``imported``, ``errors``, …). Best-effort: if the queue is already gone the run
    still finalized via the audit row.
    """
    registry = get_operation_registry()
    queue = await registry.get_queue(operation_id)
    if queue is None:
        return
    event_type = (
        WorkflowConstants.SSE_EVENT_ERROR
        if status == "error"
        else WorkflowConstants.SSE_EVENT_COMPLETE
    )
    final_status = "failed" if status == "error" else "completed"
    await queue.put(
        build_terminal_event(
            "evt_final",
            event_type,
            operation_id,
            final_status,
            run_id=run_id,
            counts=counts or {},
        )
    )


async def launch_sse_operation(
    *,
    user_id: str,
    operation_type: str,
    coro_factory: Callable[[OperationBoundEmitter], Awaitable[object]],
    name_prefix: str = "import",
    request_params: JsonDict | None = None,
    initiated_by: str = "manual",
) -> OperationStartedResponse:
    """Run the standard kickoff → background → return-202 shape.

    Six routes share this exact pattern (Last.fm/Spotify imports, likes
    sync/export, connector playlist import, bulk apply-assignments).
    Wrapping it here keeps each route handler at ~3 lines of business
    logic — define the use case call, pass the factory, return.

    The factory MUST ``return`` its use case's result (an ``OperationResult``)
    so a handled soft failure (``is_failure``) is finalized as ``error`` with
    real counts. A factory that awaits without returning yields ``None``, which
    ``audit_outcome`` can only record as ``complete`` — the dropped-result bug
    this contract exists to prevent.

    ``request_params`` is persisted on the audit row so a retryable operation can
    be re-invoked from the run alone — connector config strings only.

    ``initiated_by`` attributes the run in the log — defaults to "manual" so all
    existing callers are unaffected; the chat→launcher wiring passes "assistant"
    for AI-agent-initiated background operations.
    """
    operation_id, run_id, emitter = await prepare_sse_operation_with_emitter(
        user_id=user_id,
        operation_type=operation_type,
        request_params=request_params,
        initiated_by=initiated_by,
    )
    # Human-readable parent-op description (e.g. "import_lastfm_history" →
    # "Import Lastfm History") for the top-level `started` event.
    description = operation_type.replace("_", " ").title()
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
            description=description,
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
