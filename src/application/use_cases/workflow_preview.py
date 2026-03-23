"""Use case for workflow preview (dry-run execution).

Previews are ephemeral — no run record is created. The workflow is executed
with ``dry_run=True`` so destination nodes skip writes. Results are delivered
via SSE for real-time node status and a final ``preview_complete`` event.
"""

# pyright: reportExplicitAny=false
# Legitimate Any: SSE queue carries heterogeneous event dicts

import asyncio
from typing import Any

from attrs import define, field

from src.application.use_cases.workflow_runs import serialize_output_tracks
from src.application.utilities.timing import ExecutionTimer
from src.application.workflows.observers import NodePreviewSummary
from src.application.workflows.prefect import (
    WorkflowAlreadyRunningError,
    is_workflow_running,
)
from src.config.constants import WorkflowConstants
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
    """Execute a workflow in dry-run mode for preview.

    Validates the definition, checks connector availability, then runs the
    workflow with ``dry_run=True``. No database records are created.
    """

    async def execute(
        self,
        workflow_def: WorkflowDef,
        sse_queue: asyncio.Queue[Any] | None = None,
    ) -> PreviewWorkflowResult:
        from src.application.services.progress_manager import get_progress_manager
        from src.application.workflows.observers import PreviewNodeObserver
        from src.application.workflows.prefect import run_workflow
        from src.application.workflows.validation import validate_workflow_def

        validate_workflow_def(workflow_def)

        # Check execution guard — previews share the same guard as real runs
        if await is_workflow_running(workflow_def.id):
            raise WorkflowAlreadyRunningError(workflow_def.id)

        timer = ExecutionTimer()

        with logging_context(
            workflow_id=workflow_def.id,
            workflow_name=workflow_def.name,
            mode="preview",
        ):
            try:
                progress_manager = get_progress_manager()
                observer = PreviewNodeObserver(sse_queue=sse_queue)

                result = await run_workflow(
                    workflow_def,
                    progress_manager=progress_manager,
                    observer=observer,
                    dry_run=True,
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

            except Exception:
                logger.error("Preview execution failed", exc_info=True)
                raise
