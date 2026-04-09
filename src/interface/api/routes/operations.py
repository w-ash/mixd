"""Operation status and SSE progress stream endpoints.

Provides real-time progress streaming via Server-Sent Events and
snapshot endpoints for querying operation state.
"""

from collections.abc import AsyncGenerator
import contextlib
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.sse import EventSourceResponse, ServerSentEvent

from src.config import get_logger
from src.domain.exceptions import NotFoundError
from src.interface.api.services.progress import (
    SSE_SENTINEL,
    get_operation_registry,
)

logger = get_logger(__name__).bind(service="operations_api")

router = APIRouter(prefix="/operations", tags=["operations"])


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

        raw = await queue.get()

        if raw is SSE_SENTINEL:
            break

        if not isinstance(raw, dict):
            continue

        event_dict: dict[str, object] = raw

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
