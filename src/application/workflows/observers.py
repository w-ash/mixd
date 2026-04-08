"""Concrete NodeExecutionObserver implementations.

ProgressNodeObserver bridges the node lifecycle protocol to the existing
AsyncProgressManager, emitting progress events for CLI Rich progress bars.

RunHistoryObserver persists node execution records to the database AND pushes
SSE ``node_status`` events for live DAG visualization in the web UI. DB
persistence is injected via a ``NodeStatusUpdater`` callable so this module
stays free of infrastructure imports.

CompositeNodeObserver delegates to multiple observers, enabling CLI to get
both Rich progress bars AND database run history simultaneously.

NullNodeObserver is the null-object default — eliminates None checks in the
orchestration loop when no observer is provided.
"""

# pyright: reportAny=false
# Legitimate Any: SSE queue carries heterogeneous event dicts

import asyncio
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from attrs import define

from src.application.services.progress_manager import AsyncProgressManager
from src.config.constants import WorkflowConstants
from src.config.logging import get_logger
from src.domain.entities.progress import ProgressStatus, create_progress_event
from src.domain.entities.workflow import NodeExecutionEvent, RunStatus

from .protocols import NodeExecutionObserver, NodeResult, NodeStatusUpdater

logger = get_logger(__name__)


@define(frozen=True, slots=True)
class NodePreviewSummary:
    """Per-node summary in a preview result."""

    node_id: str
    node_type: str
    track_count: int
    sample_titles: list[str]


def _build_sse_node_event(
    counter: int,
    event: NodeExecutionEvent,
    status: RunStatus,
    *,
    run_id: UUID | None = None,
    error_message: str | None = None,
) -> dict[str, Any]:
    """Build an SSE node_status event dict, shared by all observer types."""
    data: dict[str, Any] = {
        "node_id": event.task_def.id,
        "node_type": event.task_def.type,
        "status": status,
        "execution_order": event.execution_order,
        "total_nodes": event.total_nodes,
        "duration_ms": event.duration_ms,
        "input_track_count": event.input_track_count,
        "output_track_count": event.output_track_count,
    }
    if run_id is not None:
        data["run_id"] = run_id
    if error_message:
        data["error_message"] = error_message
    return {
        "id": f"evt_{counter}",
        "event": WorkflowConstants.SSE_EVENT_NODE_STATUS,
        "data": data,
    }


async def _push_sse_node_event(
    queue: asyncio.Queue[Any] | None,
    counter: int,
    event: NodeExecutionEvent,
    status: RunStatus,
    *,
    run_id: UUID | None = None,
    error_message: str | None = None,
) -> int:
    """Push an SSE node_status event to the queue. Returns updated counter."""
    if queue is None:
        return counter
    counter += 1
    try:
        await queue.put(
            _build_sse_node_event(
                counter,
                event,
                status,
                run_id=run_id,
                error_message=error_message,
            )
        )
    except Exception:
        logger.warning(
            "Failed to push SSE node_status event",
            run_id=run_id,
            node_id=event.task_def.id,
            status=status,
            exc_info=True,
        )
    return counter


def _format_node_display_name(node_type: str) -> str:
    """Convert dotted node type to human-readable title (e.g. 'enricher.spotify' → 'Enricher Spotify')."""
    return node_type.replace("_", " ").replace(".", " ").title()


class NullNodeObserver:
    """No-op observer — eliminates None checks when no observer is provided."""

    async def on_node_starting(self, event: NodeExecutionEvent) -> None:
        pass

    async def on_node_completed(
        self, event: NodeExecutionEvent, result: NodeResult
    ) -> None:
        pass

    async def on_node_failed(self, event: NodeExecutionEvent, error: Exception) -> None:
        pass


class CompositeNodeObserver:
    """Delegates to multiple observers — enables CLI progress + DB history simultaneously."""

    _observers: list[NodeExecutionObserver]

    def __init__(self, observers: list[NodeExecutionObserver]) -> None:
        self._observers = observers

    async def on_node_starting(self, event: NodeExecutionEvent) -> None:
        for obs in self._observers:
            await obs.on_node_starting(event)

    async def on_node_completed(
        self, event: NodeExecutionEvent, result: NodeResult
    ) -> None:
        for obs in self._observers:
            await obs.on_node_completed(event, result)

    async def on_node_failed(self, event: NodeExecutionEvent, error: Exception) -> None:
        for obs in self._observers:
            await obs.on_node_failed(event, error)


class ProgressNodeObserver:
    """Emits progress events via AsyncProgressManager on node lifecycle transitions.

    Replaces the inline progress emission that was previously in execute_node().
    """

    _progress_manager: AsyncProgressManager
    _workflow_operation_id: str

    def __init__(
        self,
        progress_manager: AsyncProgressManager,
        workflow_operation_id: str,
    ) -> None:
        self._progress_manager = progress_manager
        self._workflow_operation_id = workflow_operation_id

    async def on_node_starting(self, event: NodeExecutionEvent) -> None:
        pass  # Progress bars show completion, not start

    async def on_node_completed(
        self, event: NodeExecutionEvent, result: NodeResult
    ) -> None:
        display_name = _format_node_display_name(event.task_def.type)

        progress_event = create_progress_event(
            operation_id=self._workflow_operation_id,
            current=event.execution_order,
            total=event.total_nodes,
            message=f"Completed {display_name}",
            status=ProgressStatus.IN_PROGRESS,
        )
        await self._progress_manager.emit_progress(progress_event)

    async def on_node_failed(self, event: NodeExecutionEvent, error: Exception) -> None:
        display_name = _format_node_display_name(event.task_def.type)

        progress_event = create_progress_event(
            operation_id=self._workflow_operation_id,
            current=event.execution_order,
            total=event.total_nodes,
            message=f"Failed {display_name}: {error}",
            status=ProgressStatus.FAILED,
        )
        await self._progress_manager.emit_progress(progress_event)


class PreviewNodeObserver:
    """Lightweight observer for dry-run previews — SSE only, no DB persistence.

    Tracks per-node output summaries (track count + sample titles) and pushes
    SSE ``node_status`` events for live canvas updates during preview.
    """

    _sse_queue: asyncio.Queue[Any] | None
    _event_counter: int
    _summaries: list[NodePreviewSummary]

    def __init__(self, sse_queue: asyncio.Queue[Any] | None = None) -> None:
        self._sse_queue = sse_queue
        self._event_counter = 0
        self._summaries = []

    def get_summaries(self) -> list[NodePreviewSummary]:
        """Return accumulated node summaries for the preview result."""
        return self._summaries

    async def on_node_starting(self, event: NodeExecutionEvent) -> None:
        self._event_counter = await _push_sse_node_event(
            self._sse_queue,
            self._event_counter,
            event,
            WorkflowConstants.RUN_STATUS_RUNNING,
        )

    async def on_node_completed(
        self, event: NodeExecutionEvent, result: NodeResult
    ) -> None:
        tracklist = result.get("tracklist")
        tracks = tracklist.tracks if tracklist else []
        self._summaries.append(
            NodePreviewSummary(
                node_id=event.task_def.id,
                node_type=event.task_def.type,
                track_count=len(tracks),
                sample_titles=[t.title or "Unknown" for t in tracks[:5]],
            )
        )
        self._event_counter = await _push_sse_node_event(
            self._sse_queue,
            self._event_counter,
            event,
            WorkflowConstants.RUN_STATUS_COMPLETED,
        )

    async def on_node_failed(self, event: NodeExecutionEvent, error: Exception) -> None:
        self._event_counter = await _push_sse_node_event(
            self._sse_queue,
            self._event_counter,
            event,
            WorkflowConstants.RUN_STATUS_FAILED,
            error_message=str(error),
        )


class RunHistoryObserver:
    """Persists node execution to DB and emits SSE node_status events.

    DB persistence is handled by an injected ``NodeStatusUpdater`` callable
    (provided by the interface layer) so this observer stays free of
    infrastructure imports. Each call uses a short-lived independent session
    so node status updates survive workflow failures.
    """

    _run_id: UUID
    _update_node_status_fn: NodeStatusUpdater
    _sse_queue: asyncio.Queue[Any] | None
    _event_counter: int
    _persist_failure_count: int

    def __init__(
        self,
        run_id: UUID,
        update_node_status: NodeStatusUpdater,
        sse_queue: asyncio.Queue[Any] | None = None,
    ) -> None:
        self._run_id = run_id
        self._update_node_status_fn = update_node_status
        self._sse_queue = sse_queue
        self._event_counter = 0
        self._persist_failure_count = 0

    @property
    def persist_failure_count(self) -> int:
        """Number of DB persistence failures during this observer's lifetime."""
        return self._persist_failure_count

    async def on_node_starting(self, event: NodeExecutionEvent) -> None:
        now = datetime.now(UTC)
        await self._persist_node_status(
            event,
            status=WorkflowConstants.RUN_STATUS_RUNNING,
            started_at=now,
        )
        self._event_counter = await _push_sse_node_event(
            self._sse_queue,
            self._event_counter,
            event,
            WorkflowConstants.RUN_STATUS_RUNNING,
            run_id=self._run_id,
        )

    async def on_node_completed(
        self, event: NodeExecutionEvent, result: NodeResult
    ) -> None:
        now = datetime.now(UTC)

        await self._persist_node_status(
            event,
            status=WorkflowConstants.RUN_STATUS_COMPLETED,
            completed_at=now,
            duration_ms=event.duration_ms,
            input_track_count=event.input_track_count,
            output_track_count=event.output_track_count,
            node_details=result.get("node_details"),
        )
        self._event_counter = await _push_sse_node_event(
            self._sse_queue,
            self._event_counter,
            event,
            WorkflowConstants.RUN_STATUS_COMPLETED,
            run_id=self._run_id,
        )

    async def on_node_failed(self, event: NodeExecutionEvent, error: Exception) -> None:
        now = datetime.now(UTC)
        await self._persist_node_status(
            event,
            status=WorkflowConstants.RUN_STATUS_FAILED,
            completed_at=now,
            duration_ms=event.duration_ms,
            error_message=str(error),
        )
        self._event_counter = await _push_sse_node_event(
            self._sse_queue,
            self._event_counter,
            event,
            WorkflowConstants.RUN_STATUS_FAILED,
            run_id=self._run_id,
            error_message=str(error),
        )

    # -- internal helpers --

    async def _persist_node_status(
        self,
        event: NodeExecutionEvent,
        *,
        status: RunStatus,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
        duration_ms: int | None = None,
        input_track_count: int | None = None,
        output_track_count: int | None = None,
        error_message: str | None = None,
        node_details: dict[str, Any] | None = None,
    ) -> None:
        """Delegate node status write to the injected updater."""
        try:
            await self._update_node_status_fn(
                run_id=self._run_id,
                node_id=event.task_def.id,
                status=status,
                started_at=started_at,
                completed_at=completed_at,
                duration_ms=duration_ms,
                input_track_count=input_track_count,
                output_track_count=output_track_count,
                error_message=error_message,
                node_details=node_details,
            )
        except Exception:
            self._persist_failure_count += 1
            logger.warning(
                "Failed to persist node status",
                run_id=self._run_id,
                node_id=event.task_def.id,
                status=status,
                exc_info=True,
            )
