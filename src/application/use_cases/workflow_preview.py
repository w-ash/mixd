"""Use case for workflow preview: run the graph, skip destination writes.

Previews are ephemeral (no run record) but not write-free: ``dry_run`` is
honored only by destinations, while sources materialize canonical rows
(idempotent upserts). Load-bearing, not an oversight — nodes each open their
own session and downstream nodes re-query tracks by id, so source writes must
be committed to be visible. A write-free preview would need a shared
no-commit session, and would hold uncommitted identity keys for its whole
duration — the exact lock-contention blocker the ingest retry exists to
survive.

Results are delivered via SSE, ending with a ``preview_complete`` event.
"""

import asyncio

from attrs import define, field

from src.application.use_cases.workflow_runs import serialize_output_tracks
from src.application.utilities.timing import ExecutionTimer
from src.application.workflows.engine.observers import NodePreviewSummary
from src.config.constants import BusinessLimits, WorkflowConstants
from src.config.logging import get_logger, logging_context
from src.domain.entities.workflow import WorkflowDef

logger = get_logger(__name__).bind(service="workflow_preview")


@define(frozen=True, slots=True)
class PreviewWorkflowResult:
    """Result of a preview execution."""

    output_tracks: list[dict[str, object]]
    node_summaries: list[NodePreviewSummary]
    duration_ms: int
    total_track_count: int = 0
    metric_columns: list[str] = field(factory=list)


@define(slots=True)
class PreviewWorkflowUseCase:
    """Execute a workflow in preview mode (destination writes skipped).

    Validates the definition, then runs the workflow with ``dry_run=True``.
    Source nodes still materialize canonical rows — see the module docstring.
    """

    async def execute(
        self,
        workflow_def: WorkflowDef,
        sse_queue: asyncio.Queue[object] | None = None,
        user_id: str = BusinessLimits.DEFAULT_USER_ID,
    ) -> PreviewWorkflowResult:
        from src.application.workflows.definition.validation import (
            validate_workflow_def,
        )

        validate_workflow_def(workflow_def)

        # Guards (active-run 409, concurrency slot) live in the API kickoff;
        # this use case stays guard-free so the chat tool can call it directly.
        # A residual overlap degrades to write contention, which is retried.

        timer = ExecutionTimer()

        with logging_context(
            workflow_id=workflow_def.id,
            workflow_name=workflow_def.name,
            mode="preview",
        ):
            try:
                return await self._run_preview(workflow_def, sse_queue, user_id, timer)

            except Exception:
                logger.error("Preview execution failed", exc_info=True)
                raise

    async def _run_preview(
        self,
        workflow_def: WorkflowDef,
        sse_queue: asyncio.Queue[object] | None,
        user_id: str,
        timer: ExecutionTimer,
    ) -> PreviewWorkflowResult:
        """Run the workflow as a dry-run preview and build the result.

        Extracted from ``execute`` so the protective ``try`` clause stays small;
        the same statements remain guarded by the caller's broad ``except``.
        """
        from src.application.services.progress_broker import get_progress_broker
        from src.application.workflows.engine.executor import run_workflow
        from src.application.workflows.engine.observers import PreviewNodeObserver

        progress_broker = get_progress_broker()
        observer = PreviewNodeObserver(sse_queue=sse_queue)

        result = await run_workflow(
            workflow_def,
            progress_broker=progress_broker,
            observer=observer,
            dry_run=True,
            user_id=user_id,
        )

        duration_ms = timer.stop()
        total_track_count = len(result.tracks) if result.tracks else 0
        if result.tracks:
            output_tracks, metric_columns = serialize_output_tracks(
                result.tracks,
                limit=WorkflowConstants.PREVIEW_OUTPUT_LIMIT,
                metrics=result.metrics,
            )
        else:
            output_tracks, metric_columns = [], []
        node_summaries = observer.get_summaries()

        return PreviewWorkflowResult(
            output_tracks=output_tracks,
            node_summaries=node_summaries,
            duration_ms=duration_ms,
            total_track_count=total_track_count,
            metric_columns=metric_columns,
        )
