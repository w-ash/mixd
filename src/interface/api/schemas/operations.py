"""Pydantic schemas for the operations REST surface.

The SSE stream remains the primary progress channel; this snapshot
endpoint exists so the frontend's watchdog (45 s without any frame)
can recover terminal state from the DB after a stall.
"""

from datetime import datetime

from pydantic import BaseModel


class OperationSnapshotNodeSchema(BaseModel):
    """One row from ``workflow_run_nodes``, shaped to mirror node_status SSE events."""

    node_id: str
    node_type: str
    status: str
    execution_order: int
    duration_ms: int | None = None
    input_track_count: int | None = None
    output_track_count: int | None = None
    error_message: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


class OperationSnapshotResponse(BaseModel):
    """Persisted state for an operation_id, used as REST fallback for SSE stalls."""

    operation_id: str
    run_id: str
    workflow_id: str
    status: str
    is_terminal: bool
    error_message: str | None = None
    heartbeat_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    output_track_count: int | None = None
    duration_ms: int | None = None
    nodes: list[OperationSnapshotNodeSchema]
