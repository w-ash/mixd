"""Pydantic schemas for the operations REST surface.

The SSE stream remains the primary progress channel; this snapshot
endpoint exists so the frontend's watchdog (45 s without any frame)
can recover terminal state from the DB after a stall.
"""

from pydantic import Field

from src.interface.api.schemas.workflows import (
    WorkflowRunNodeSchema,
    WorkflowRunSummarySchema,
)


class OperationSnapshotResponse(WorkflowRunSummarySchema):
    """Persisted state for an ``operation_id``, used as REST fallback for SSE stalls.

    Extends the run summary (which already carries the ``operation_id`` SSE
    handle) with the persisted node list. ``is_terminal`` is derived client-side
    from ``status`` to avoid duplicating the terminal-status set on the wire.
    """

    nodes: list[WorkflowRunNodeSchema] = Field(default_factory=list)
