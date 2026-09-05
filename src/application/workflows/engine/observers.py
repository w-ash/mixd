"""Concrete NodeExecutionObserver implementations.

ProgressNodeObserver bridges the node lifecycle protocol to the existing
ProgressBroker, emitting progress events for CLI Rich progress bars.

RunHistoryObserver persists node execution records to the database AND pushes
SSE ``node_status`` events for live DAG visualization in the web UI. DB
persistence is injected via a ``NodeStatusUpdater`` callable so this module
stays free of infrastructure imports.

CompositeNodeObserver delegates to multiple observers, enabling CLI to get
both Rich progress bars AND database run history simultaneously.

NullNodeObserver is the null-object default — eliminates None checks in the
orchestration loop when no observer is provided.
"""

import asyncio
from collections.abc import Awaitable, Coroutine
from datetime import UTC, datetime
from uuid import UUID

from attrs import define

from src.application.services.progress_broker import ProgressBroker
from src.application.workflows.protocols import (
    NodeExecutionObserver,
    NodeResult,
    NodeStatusUpdater,
)
from src.config.constants import WorkflowConstants
from src.config.logging import get_logger
from src.domain.entities.progress import ProgressStatus, create_progress_event
from src.domain.entities.workflow import NodeExecutionEvent, RunStatus

logger = get_logger(__name__)


@define(frozen=True, slots=True)
class NodePreviewSummary:
    """Per-node summary in a preview result."""

    node_id: str
    node_type: str
    track_count: int
    sample_titles: list[str]


class _SseEmittingObserver:
    """Shared SSE ``node_status`` emission for observers that feed a live canvas.

    Holds the queue and the monotonic event counter; subclasses call ``_emit``
    from their lifecycle hooks. A ``None`` queue makes emission a no-op.
    """

    _sse_queue: asyncio.Queue[object] | None
    _event_counter: int
    _sse_run_id: UUID | None

    def __init__(
        self, sse_queue: asyncio.Queue[object] | None, run_id: UUID | None = None
    ) -> None:
        self._sse_queue = sse_queue
        self._event_counter = 0
        self._sse_run_id = run_id

    async def _emit(
        self,
        event: NodeExecutionEvent,
        status: RunStatus,
        *,
        error_message: str | None = None,
    ) -> None:
        """Push one SSE node_status event; failures are logged, never raised."""
        if self._sse_queue is None:
            return
        self._event_counter += 1
        data: dict[str, object] = {
            "node_id": event.task_def.id,
            "node_type": event.task_def.type,
            "status": status,
            "execution_order": event.execution_order,
            "total_nodes": event.total_nodes,
            "duration_ms": event.duration_ms,
            "input_track_count": event.input_track_count,
            "output_track_count": event.output_track_count,
        }
        if self._sse_run_id is not None:
            data["run_id"] = self._sse_run_id
        if error_message:
            data["error_message"] = error_message
        try:
            await self._sse_queue.put({
                "id": f"evt_{self._event_counter}",
                "event": WorkflowConstants.SSE_EVENT_NODE_STATUS,
                "data": data,
            })
        except Exception:
            logger.warning(
                "Failed to push SSE node_status event",
                run_id=self._sse_run_id,
                node_id=event.task_def.id,
                status=status,
                exc_info=True,
            )


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
    """Delegates to multiple observers — enables CLI progress + DB history simultaneously.

    Observers run concurrently and are isolated from each other: one
    observer's failure is logged and never reaches the others or the
    executing node. ``asyncio.gather(return_exceptions=True)`` is used
    instead of a ``TaskGroup`` for the same reason as
    ``ProgressBroker._broadcast`` — a TaskGroup would propagate the first
    failure and cancel its siblings, breaking the isolation contract.
    Cancelling the awaiting task still cancels the fan-out and re-raises,
    so external cancellation is honoured.
    """

    _observers: list[NodeExecutionObserver]

    def __init__(self, observers: list[NodeExecutionObserver]) -> None:
        self._observers = observers

    async def _fan_out(self, hook: str, calls: list[Awaitable[None]]) -> None:
        """Await every observer call; log failures per observer, never raise."""
        results = await asyncio.gather(*calls, return_exceptions=True)
        for obs, result in zip(self._observers, results, strict=True):
            if isinstance(result, BaseException):
                logger.warning(
                    "Node observer raised; other observers unaffected",
                    hook=hook,
                    observer=type(obs).__name__,
                    exc_info=result,
                )

    async def on_node_starting(self, event: NodeExecutionEvent) -> None:
        await self._fan_out(
            "on_node_starting", [obs.on_node_starting(event) for obs in self._observers]
        )

    async def on_node_completed(
        self, event: NodeExecutionEvent, result: NodeResult
    ) -> None:
        await self._fan_out(
            "on_node_completed",
            [obs.on_node_completed(event, result) for obs in self._observers],
        )

    async def on_node_failed(self, event: NodeExecutionEvent, error: Exception) -> None:
        await self._fan_out(
            "on_node_failed",
            [obs.on_node_failed(event, error) for obs in self._observers],
        )


class ProgressNodeObserver:
    """Emits progress events via ProgressBroker on node lifecycle transitions.

    Replaces the inline progress emission that was previously in execute_node().
    """

    _progress_broker: ProgressBroker
    _workflow_operation_id: str

    def __init__(
        self,
        progress_broker: ProgressBroker,
        workflow_operation_id: str,
    ) -> None:
        self._progress_broker = progress_broker
        self._workflow_operation_id = workflow_operation_id

    async def on_node_starting(self, event: NodeExecutionEvent) -> None:
        pass  # Progress bars show completion, not start

    async def on_node_completed(
        self, event: NodeExecutionEvent, result: NodeResult
    ) -> None:
        del result  # protocol-required, but progress bars surface lifecycle, not output
        display_name = _format_node_display_name(event.task_def.type)

        progress_event = create_progress_event(
            operation_id=self._workflow_operation_id,
            current=event.execution_order,
            total=event.total_nodes,
            message=f"Completed {display_name}",
            status=ProgressStatus.IN_PROGRESS,
        )
        await self._progress_broker.emit_progress(progress_event)

    async def on_node_failed(self, event: NodeExecutionEvent, error: Exception) -> None:
        display_name = _format_node_display_name(event.task_def.type)

        progress_event = create_progress_event(
            operation_id=self._workflow_operation_id,
            current=event.execution_order,
            total=event.total_nodes,
            message=f"Failed {display_name}: {error}",
            status=ProgressStatus.FAILED,
        )
        await self._progress_broker.emit_progress(progress_event)


class PreviewNodeObserver(_SseEmittingObserver):
    """Lightweight observer for dry-run previews — SSE only, no DB persistence.

    Tracks per-node output summaries (track count + sample titles) and pushes
    SSE ``node_status`` events for live canvas updates during preview.
    """

    _summaries: list[NodePreviewSummary]

    def __init__(self, sse_queue: asyncio.Queue[object] | None = None) -> None:
        super().__init__(sse_queue)
        self._summaries = []

    def get_summaries(self) -> list[NodePreviewSummary]:
        """Return accumulated node summaries for the preview result."""
        return self._summaries

    async def on_node_starting(self, event: NodeExecutionEvent) -> None:
        await self._emit(event, WorkflowConstants.RUN_STATUS_RUNNING)

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
        await self._emit(event, WorkflowConstants.RUN_STATUS_COMPLETED)

    async def on_node_failed(self, event: NodeExecutionEvent, error: Exception) -> None:
        await self._emit(
            event, WorkflowConstants.RUN_STATUS_FAILED, error_message=str(error)
        )


class RunHistoryObserver(_SseEmittingObserver):
    """Persists node execution to DB and emits SSE node_status events.

    DB persistence is handled by an injected ``NodeStatusUpdater`` callable
    (provided by the interface layer) so this observer stays free of
    infrastructure imports. Each call uses a short-lived independent session
    so node status updates survive workflow failures.

    The DB write and the SSE push run concurrently per hook; each swallows
    its own errors, so a slow or failing DB never delays the live canvas.
    """

    _run_id: UUID
    _update_node_status_fn: NodeStatusUpdater
    _persist_failure_count: int

    def __init__(
        self,
        run_id: UUID,
        update_node_status: NodeStatusUpdater,
        sse_queue: asyncio.Queue[object] | None = None,
    ) -> None:
        super().__init__(sse_queue, run_id)
        self._run_id = run_id
        self._update_node_status_fn = update_node_status
        self._persist_failure_count = 0

    @property
    def persist_failure_count(self) -> int:
        """Number of DB persistence failures during this observer's lifetime."""
        return self._persist_failure_count

    async def on_node_starting(self, event: NodeExecutionEvent) -> None:
        await self._persist_and_emit(
            self._persist_node_status(
                event,
                status=WorkflowConstants.RUN_STATUS_RUNNING,
                started_at=datetime.now(UTC),
            ),
            self._emit(event, WorkflowConstants.RUN_STATUS_RUNNING),
        )

    async def on_node_completed(
        self, event: NodeExecutionEvent, result: NodeResult
    ) -> None:
        await self._persist_and_emit(
            self._persist_node_status(
                event,
                status=WorkflowConstants.RUN_STATUS_COMPLETED,
                completed_at=datetime.now(UTC),
                duration_ms=event.duration_ms,
                input_track_count=event.input_track_count,
                output_track_count=event.output_track_count,
                node_details=result.get("node_details"),
            ),
            self._emit(event, WorkflowConstants.RUN_STATUS_COMPLETED),
        )

    async def on_node_failed(self, event: NodeExecutionEvent, error: Exception) -> None:
        await self._persist_and_emit(
            self._persist_node_status(
                event,
                status=WorkflowConstants.RUN_STATUS_FAILED,
                completed_at=datetime.now(UTC),
                duration_ms=event.duration_ms,
                error_message=str(error),
            ),
            self._emit(
                event, WorkflowConstants.RUN_STATUS_FAILED, error_message=str(error)
            ),
        )

    # -- internal helpers --

    @staticmethod
    async def _persist_and_emit(
        persist: Coroutine[object, object, None],
        emit: Coroutine[object, object, None],
    ) -> None:
        """Run the DB write and the SSE push concurrently.

        Both coroutines swallow their own errors, so the TaskGroup only
        provides structured cancellation — never a sibling-cancelling failure.
        """
        async with asyncio.TaskGroup() as tg:
            tg.create_task(persist)
            tg.create_task(emit)

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
        node_details: dict[str, object] | None = None,
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
