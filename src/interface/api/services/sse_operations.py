"""Shared SSE operation setup and terminal event construction.

Eliminates duplication across import, playlist, and workflow route handlers.
Each route file still owns its background lifecycle logic — this module only
provides the common setup (uuid + queue registration) and terminal event format.
"""

# pyright: reportExplicitAny=false, reportAny=false
# Legitimate Any: SSE event data values are heterogeneous

import asyncio
from typing import Any
from uuid import UUID, uuid4

from src.interface.api.services.progress import get_operation_registry


async def prepare_sse_operation() -> tuple[str, asyncio.Queue[Any]]:
    """Generate an operation_id, register an SSE queue, and return both.

    This is the minimal shared setup. Route-specific guards (e.g. the 429
    concurrency limit in imports) wrap this function rather than replacing it.
    """
    operation_id = str(uuid4())
    registry = get_operation_registry()
    sse_queue = await registry.register(operation_id)
    return operation_id, sse_queue


def build_terminal_event(
    event_id: str,
    event_type: str,
    operation_id: str,
    status: Any,
    *,
    run_id: UUID | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Build a terminal SSE event dict with shared structure.

    Used by playlist sync (complete/error), workflow runs, and workflow
    previews to construct the final event pushed to the SSE queue.
    """
    data: dict[str, Any] = {
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
