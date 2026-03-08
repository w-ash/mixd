"""Concrete NodeExecutionObserver implementations.

ProgressNodeObserver bridges the node lifecycle protocol to the existing
AsyncProgressManager, emitting progress events for CLI Rich progress bars.

RunHistoryObserver persists node execution records to the database AND pushes
SSE ``node_status`` events for live DAG visualization in the web UI. DB
persistence is injected via a ``NodeStatusUpdater`` callable so this module
stays free of infrastructure imports.

NullNodeObserver is the null-object default — eliminates None checks in the
orchestration loop when no observer is provided.
"""

# pyright: reportExplicitAny=false
# Legitimate Any: SSE queue carries heterogeneous event dicts

import asyncio
from datetime import UTC, datetime
from typing import Any

import attrs

from src.application.services.progress_manager import AsyncProgressManager
from src.config.constants import WorkflowConstants
from src.config.logging import get_logger
from src.domain.entities.progress import ProgressStatus, create_progress_event
from src.domain.entities.workflow import NodeExecutionEvent, RunStatus

from .protocols import NodeResult, NodeStatusUpdater

logger = get_logger(__name__)


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


class RunHistoryObserver:
    """Persists node execution to DB and emits SSE node_status events.

    DB persistence is handled by an injected ``NodeStatusUpdater`` callable
    (provided by the interface layer) so this observer stays free of
    infrastructure imports. Each call uses a short-lived independent session
    so node status updates survive workflow failures.
    """

    _run_id: int
    _update_node_status_fn: NodeStatusUpdater
    _sse_queue: asyncio.Queue[Any] | None
    _event_counter: int
    _persist_failure_count: int

    def __init__(
        self,
        run_id: int,
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
        await self._push_sse(event, WorkflowConstants.RUN_STATUS_RUNNING)

    async def on_node_completed(
        self, event: NodeExecutionEvent, result: NodeResult
    ) -> None:
        now = datetime.now(UTC)

        # Serialize track_decisions to JSON-safe dict for node_details
        node_details: dict[str, Any] | None = None
        decisions = result.get("track_decisions")
        if decisions:
            node_details = {
                "track_decisions": [attrs.asdict(d, recurse=False) for d in decisions],
            }

        await self._persist_node_status(
            event,
            status=WorkflowConstants.RUN_STATUS_COMPLETED,
            completed_at=now,
            duration_ms=event.duration_ms,
            input_track_count=event.input_track_count,
            output_track_count=event.output_track_count,
            node_details=node_details,
        )
        await self._push_sse(event, WorkflowConstants.RUN_STATUS_COMPLETED)

    async def on_node_failed(self, event: NodeExecutionEvent, error: Exception) -> None:
        now = datetime.now(UTC)
        await self._persist_node_status(
            event,
            status=WorkflowConstants.RUN_STATUS_FAILED,
            completed_at=now,
            duration_ms=event.duration_ms,
            error_message=str(error),
        )
        await self._push_sse(
            event, WorkflowConstants.RUN_STATUS_FAILED, error_message=str(error)
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
            logger.opt(exception=True).warning(
                "Failed to persist node status",
                run_id=self._run_id,
                node_id=event.task_def.id,
                status=status,
            )

    async def _push_sse(
        self,
        event: NodeExecutionEvent,
        status: RunStatus,
        *,
        error_message: str | None = None,
    ) -> None:
        """Push a node_status SSE event to the queue if connected."""
        if self._sse_queue is None:
            return

        try:
            self._event_counter += 1
            sse_event: dict[str, Any] = {
                "id": f"evt_{self._event_counter}",
                "event": WorkflowConstants.SSE_EVENT_NODE_STATUS,
                "data": {
                    "run_id": self._run_id,
                    "node_id": event.task_def.id,
                    "node_type": event.task_def.type,
                    "status": status,
                    "execution_order": event.execution_order,
                    "total_nodes": event.total_nodes,
                    "duration_ms": event.duration_ms,
                    "input_track_count": event.input_track_count,
                    "output_track_count": event.output_track_count,
                },
            }
            if error_message:
                sse_event["data"]["error_message"] = error_message

            await self._sse_queue.put(sse_event)
        except Exception:
            logger.opt(exception=True).warning(
                "Failed to push SSE node_status event",
                run_id=self._run_id,
                node_id=event.task_def.id,
                status=status,
            )
