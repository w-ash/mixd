"""Operation status and SSE progress stream endpoints.

Provides real-time progress streaming via Server-Sent Events and
snapshot endpoints for querying operation state.
"""

import asyncio
from collections.abc import AsyncGenerator
import contextlib
from typing import Annotated, Final, cast

from fastapi import APIRouter, Depends, Request
from fastapi.sse import EventSourceResponse, ServerSentEvent

from src.application.runner import execute_use_case
from src.application.use_cases.get_operation_snapshot import (
    GetOperationSnapshotCommand,
    GetOperationSnapshotUseCase,
)
from src.config import get_logger
from src.domain.entities.workflow import WorkflowRun
from src.domain.exceptions import NotFoundError
from src.interface.api.deps import get_current_user_id
from src.interface.api.schemas.operations import OperationSnapshotResponse
from src.interface.api.schemas.workflows import WorkflowRunNodeSchema
from src.interface.api.services.progress import (
    SSE_SENTINEL,
    get_operation_registry,
)

logger = get_logger(__name__).bind(service="operations_api")

router = APIRouter(prefix="/operations", tags=["operations"])

# Server-side SSE keepalive interval. Beats Fly/Cloudflare proxy idle
# timeouts (~60s) and gives the client a "still alive" signal that's
# independent of workflow event emission cadence.
_SSE_KEEPALIVE_INTERVAL_SECONDS: Final = 15


async def _require_queue(operation_id: str) -> str:
    """Dependency: validate the operation exists before streaming.

    Runs before the generator starts, so a missing operation returns
    a proper 404 JSON error instead of opening an empty SSE stream.
    """
    registry = get_operation_registry()
    queue = await registry.get_queue(operation_id)
    if queue is None:
        raise NotFoundError(f"Operation {operation_id} not found")
    return operation_id


@router.get("/{operation_id}/progress", response_class=EventSourceResponse)
async def stream_operation_progress(
    operation_id: Annotated[str, Depends(_require_queue)],
    request: Request,
) -> AsyncGenerator[ServerSentEvent]:
    """Stream real-time progress for an operation via Server-Sent Events.

    Uses FastAPI's built-in EventSourceResponse encoding which provides:
    - Automatic 15s keep-alive pings (prevents proxy timeouts)
    - Cache-Control: no-cache and X-Accel-Buffering: no headers
    - Proper SSE wire-format encoding including multi-line data handling
    """
    registry = get_operation_registry()
    queue = await registry.get_queue(operation_id)
    if queue is None:
        return

    # Determine the last event sequence number for reconnection filtering
    last_seq = 0
    last_event_id = request.headers.get("Last-Event-ID")
    if last_event_id and last_event_id.startswith("evt_"):
        with contextlib.suppress(ValueError):
            last_seq = int(last_event_id.removeprefix("evt_"))

    while True:
        if await request.is_disconnected():
            logger.debug("SSE client disconnected", operation_id=operation_id)
            break

        try:
            raw = await asyncio.wait_for(
                queue.get(), timeout=_SSE_KEEPALIVE_INTERVAL_SECONDS
            )
        except TimeoutError:
            # Comment frame: keeps the connection alive without delivering
            # an event. EventSource clients ignore lines starting with ":".
            yield ServerSentEvent(comment="keepalive")
            continue

        if raw is SSE_SENTINEL:
            break

        if not isinstance(raw, dict):
            continue

        event_dict = cast("dict[str, object]", raw)

        # Reconnection support: skip events the client already received
        event_id = str(event_dict.get("id", ""))
        if last_seq > 0 and event_id.startswith("evt_"):
            try:
                seq = int(event_id.removeprefix("evt_"))
                if seq <= last_seq:
                    continue
            except ValueError:
                pass

        event_type = event_dict.get("event")
        yield ServerSentEvent(
            data=event_dict["data"],
            event=str(event_type) if event_type is not None else None,
            id=event_id or None,
        )


@router.get("")
async def list_active_operations() -> dict[str, list[str]]:
    """List all active operation IDs."""
    registry = get_operation_registry()
    ids = await registry.get_active_operation_ids()
    return {"operation_ids": ids}


@router.get("/{operation_id}/snapshot")
async def get_operation_snapshot(
    operation_id: str,
    user_id: str = Depends(get_current_user_id),
) -> OperationSnapshotResponse:
    """Persisted-state snapshot for an operation_id.

    Used by the frontend's watchdog (45 s without any SSE frame) to
    recover terminal state from the DB. Sweeper-marked-failed runs
    surface here even when the terminal SSE event was never delivered.

    404 if the operation_id has no matching run row, or if the calling
    user doesn't own the workflow that produced it. Authorization is
    enforced inside the use case via the workflow lookup.
    """
    command = GetOperationSnapshotCommand(user_id=user_id, operation_id=operation_id)
    result = await execute_use_case(
        lambda uow: GetOperationSnapshotUseCase().execute(command, uow),
        user_id=user_id,
    )
    return _to_snapshot(operation_id, result.run)


def _to_snapshot(operation_id: str, run: WorkflowRun) -> OperationSnapshotResponse:
    return OperationSnapshotResponse(
        id=run.id,
        workflow_id=run.workflow_id,
        run_number=run.run_number,
        status=run.status,
        definition_version=run.definition_version,
        started_at=run.started_at,
        completed_at=run.completed_at,
        heartbeat_at=run.heartbeat_at,
        duration_ms=run.duration_ms,
        output_track_count=run.output_track_count,
        output_playlist_id=run.output_playlist_id,
        error_message=run.error_message,
        created_at=run.created_at,
        operation_id=operation_id,
        nodes=[WorkflowRunNodeSchema.model_validate(n) for n in run.nodes],
    )
