"""Pydantic payload schemas for the SSE events the API streams.

Every frame pushed onto an SSE queue is ``{"id", "event", "data"}``; this module
types the ``data`` half. Emitters build the model instead of a dict literal, so
a payload is validated where it is produced, and ``scripts/export_openapi.py``
merges these schemas into ``components.schemas`` so the web client generates the
matching TypeScript union instead of casting field by field.

``SSE_EVENT_SCHEMAS`` maps every ``WorkflowConstants.SSE_EVENT_*`` name to its
model. ``complete`` and ``error`` share one model: the two frames carry the same
envelope and differ only in which optional payload fields the producer fills.
"""

from collections.abc import Mapping
from datetime import datetime
from typing import Final, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from src.config.constants import SubOperationOutcome, WorkflowConstants
from src.domain.entities.workflow import RunStatus
from src.interface.api.schemas.cache_tags import CacheTag

# Lifecycle status of a tracked operation (``OperationStatus`` values).
type SseOperationStatus = Literal[
    "pending", "running", "completed", "failed", "cancelled"
]
# Status of one progress event (``ProgressStatus`` values).
type SseProgressStatus = Literal["in_progress", "completed", "failed", "cancelled"]
# Terminal verdict on a frame that closes an operation, a sub-operation or a
# workflow run. Aliases ``RunStatus`` rather than restating its six members:
# both a long operation's ``OperationStatus`` and a workflow run's own
# ``RunStatus`` reach ``final_status``, and ``RunStatus`` is the wider of the
# two (``crashed`` is workflow-only) so it is the one alias that already covers
# both producers.
type SseFinalStatus = RunStatus
# Per-item verdict a connector-playlist import reports on its own sub-operation.
# Aliases the shared vocabulary so producer and wire can never drift.
type SseSubOperationOutcome = SubOperationOutcome

_STRICT: Final = ConfigDict(extra="forbid")


class SseOperationStartedEvent(BaseModel):
    """``started`` — the request operation opened its own stream."""

    model_config = _STRICT

    operation_id: str
    description: str
    total: int | None = None
    status: SseOperationStatus


class SseOperationProgressEvent(BaseModel):
    """``progress`` — a tick on the operation the client is attached to."""

    model_config = _STRICT

    operation_id: str
    current: int
    total: int | None = None
    message: str
    status: SseProgressStatus
    completion_percentage: float | None = None
    items_per_second: float | None = None
    eta_seconds: float | None = None


class _AncestorRouted(BaseModel):
    """Routing ids a fan-out frame carries in addition to its own payload.

    ``parent_operation_id`` is the stream the frame was delivered on;
    ``item_operation_id`` is the operation directly beneath it, so a client
    renders a nested event against the right row without rebuilding the tree.
    """

    model_config = _STRICT

    parent_operation_id: str | None = None
    item_operation_id: str | None = None


class SseSubOperationStartedEvent(_AncestorRouted):
    """``sub_operation_started`` — a child operation opened, seen from above."""

    operation_id: str
    description: str
    total: int | None = None
    # Free-form: the phase vocabulary is producer-defined (``fetch``, ``resolve``,
    # ``done``, ``match``, …) and wider than ``config.constants.Phase``.
    phase: str | None = None
    node_type: str | None = None
    connector_playlist_identifier: str | None = None
    playlist_name: str | None = None
    status: SseOperationStatus


class SseSubProgressEvent(_AncestorRouted):
    """``sub_progress`` — a child operation's tick, seen from an ancestor."""

    operation_id: str
    current: int
    total: int | None = None
    message: str
    status: SseProgressStatus
    completion_percentage: float | None = None
    phase: str | None = None
    outcome: SseSubOperationOutcome | None = None
    resolved: int | None = None
    unresolved: int | None = None
    canonical_playlist_id: str | None = None
    connector_playlist_identifier: str | None = None
    playlist_name: str | None = None
    error_message: str | None = None


class SseSubOperationCompletedEvent(_AncestorRouted):
    """``sub_operation_completed`` — a child operation settled.

    A child that is itself an audited run signs off with ``run_id``, ``counts``
    and the per-item cache tags; a plain sub-operation carries only its verdict.
    """

    operation_id: str
    final_status: SseFinalStatus
    run_id: UUID | None = None
    counts: dict[str, object] | None = None
    touched: list[CacheTag] | None = None


class SseOperationTerminalEvent(BaseModel):
    """``complete`` / ``error`` — the frame that closes an operation stream.

    One model for both frames and for all three producers (long operations,
    workflow runs, workflow previews): they share the envelope and differ only
    in which optional fields the producer fills — ``counts`` for a long
    operation, ``output_track_count``/``duration_ms`` for a finished run,
    ``error_message`` for any failure.
    """

    model_config = _STRICT

    operation_id: str
    final_status: SseFinalStatus
    run_id: UUID | None = None
    touched: list[CacheTag] | None = None
    counts: dict[str, object] | None = None
    output_track_count: int | None = None
    duration_ms: int | None = None
    error_message: str | None = None


class SseRunAcceptedEvent(BaseModel):
    """``run_accepted`` — the run row exists and background execution is queued."""

    model_config = _STRICT

    operation_id: str
    run_id: UUID
    # The definition slug (``WorkflowDef.id``), not the workflow row's UUID.
    workflow_id: str
    task_count: int
    accepted_at: datetime


class SseNodePreviewSummary(BaseModel):
    """One node's contribution to a preview result."""

    model_config = _STRICT

    node_id: str
    node_type: str
    track_count: int
    sample_titles: list[str]


class SsePreviewCompleteEvent(BaseModel):
    """``preview_complete`` — a dry run finished, with its output tracklist."""

    model_config = _STRICT

    operation_id: str
    final_status: SseFinalStatus
    touched: list[CacheTag] | None = None
    output_tracks: list[dict[str, object]]
    total_track_count: int
    metric_columns: list[str]
    node_summaries: list[SseNodePreviewSummary]
    duration_ms: int | None = None


class SseNodeStatusEvent(BaseModel):
    """``node_status`` — one workflow node changed state, for the live canvas."""

    model_config = _STRICT

    node_id: str
    node_type: str
    status: RunStatus
    execution_order: int
    total_nodes: int
    duration_ms: int | None = None
    input_track_count: int | None = None
    output_track_count: int | None = None
    run_id: UUID | None = None
    error_message: str | None = None


SSE_EVENT_SCHEMAS: Final[Mapping[str, type[BaseModel]]] = {
    WorkflowConstants.SSE_EVENT_STARTED: SseOperationStartedEvent,
    WorkflowConstants.SSE_EVENT_PROGRESS: SseOperationProgressEvent,
    WorkflowConstants.SSE_EVENT_SUB_OPERATION_STARTED: SseSubOperationStartedEvent,
    WorkflowConstants.SSE_EVENT_SUB_PROGRESS: SseSubProgressEvent,
    WorkflowConstants.SSE_EVENT_SUB_OPERATION_COMPLETED: SseSubOperationCompletedEvent,
    WorkflowConstants.SSE_EVENT_COMPLETE: SseOperationTerminalEvent,
    WorkflowConstants.SSE_EVENT_ERROR: SseOperationTerminalEvent,
    WorkflowConstants.SSE_EVENT_RUN_ACCEPTED: SseRunAcceptedEvent,
    WorkflowConstants.SSE_EVENT_PREVIEW_COMPLETE: SsePreviewCompleteEvent,
    WorkflowConstants.SSE_EVENT_NODE_STATUS: SseNodeStatusEvent,
}
"""Every SSE event name mapped to the model describing its ``data`` payload."""


def sse_frame(event_id: str, event_type: str, payload: BaseModel) -> dict[str, object]:
    """Wrap a validated payload in the queue frame the SSE route consumes.

    ``exclude_unset`` keeps each producer's key set exactly as it built it: a
    model shared by several producers (the terminal frame, the sub-operation
    terminal) must not sprout null keys for the fields this producer never had.
    """
    return {
        "id": event_id,
        "event": event_type,
        "data": payload.model_dump(mode="json", exclude_unset=True),
    }
