"""Background workflow execution with SSE lifecycle (interface concern only).

Business logic (run status management, workflow execution) lives in
``ExecuteWorkflowRunUseCase`` / ``PreviewWorkflowUseCase``; these helpers own
only SSE event emission, heartbeat scheduling, and operation cleanup.

Run/node status updaters + heartbeat ticker are shared with the CLI — see
``src/interface/_shared/run_lifecycle.py``. Both interfaces inject these into
``ExecuteWorkflowRunUseCase`` so the run lifecycle lives in exactly one place.
"""

import asyncio
from asyncio import CancelledError
import contextlib
from uuid import UUID

from src.application.use_cases.workflow_runs import ExecuteWorkflowRunUseCase
from src.config import get_logger
from src.config.constants import WorkflowConstants, truncate_error_message
from src.domain.entities.workflow import WorkflowDef
from src.interface._shared.run_lifecycle import (
    heartbeat_loop,
    update_node_status,
    update_run_status,
)
from src.interface.api.services.background import finalize_sse_operation
from src.interface.api.services.sse_operations import build_terminal_event

logger = get_logger(__name__).bind(service="workflows_api")


async def _run_workflow_and_push_terminal(
    operation_id: str,
    workflow_def: WorkflowDef,
    run_id: UUID,
    sse_queue: asyncio.Queue[object],
    user_id: str,
) -> None:
    """Run the workflow use case and push the terminal SSE event for its result."""
    use_case = ExecuteWorkflowRunUseCase(
        update_run_status=update_run_status,
        update_node_status=update_node_status,
    )
    run_result = await use_case.execute(
        workflow_def, run_id, sse_queue=sse_queue, user_id=user_id
    )

    # Push terminal SSE event based on use case result
    if run_result.status == WorkflowConstants.RUN_STATUS_COMPLETED:
        await sse_queue.put(
            build_terminal_event(
                "evt_final",
                WorkflowConstants.SSE_EVENT_COMPLETE,
                operation_id,
                WorkflowConstants.RUN_STATUS_COMPLETED,
                run_id=run_id,
                output_track_count=run_result.output_track_count,
                duration_ms=run_result.duration_ms,
            )
        )
    else:
        await sse_queue.put(
            build_terminal_event(
                "evt_error",
                WorkflowConstants.SSE_EVENT_ERROR,
                operation_id,
                run_result.status,
                run_id=run_id,
                error_message=truncate_error_message(
                    run_result.error_message or "Unknown error",
                    WorkflowConstants.SSE_ERROR_MAX_LENGTH,
                ),
            )
        )


async def execute_workflow_background(
    operation_id: str,
    workflow_def: WorkflowDef,
    run_id: UUID,
    sse_queue: asyncio.Queue[object],
    user_id: str,
) -> None:
    """Execute workflow in background, pushing SSE events for the run lifecycle.

    Delegates all business logic (run status management, workflow execution)
    to ``ExecuteWorkflowRunUseCase`` in the application layer. This function
    only handles SSE event emission and cleanup.
    """
    logger.info(
        "BG task entered",
        run_id=str(run_id),
        operation_id=operation_id,
        workflow_id=workflow_def.id,
    )
    heartbeat_task = asyncio.create_task(
        heartbeat_loop(run_id), name=f"workflow_heartbeat_{run_id}"
    )
    logger.info("Heartbeat task scheduled", run_id=str(run_id))
    try:
        await _run_workflow_and_push_terminal(
            operation_id, workflow_def, run_id, sse_queue, user_id
        )
    except CancelledError:
        # Best-effort push of error SSE event on cancellation
        with contextlib.suppress(CancelledError, Exception):
            await sse_queue.put(
                build_terminal_event(
                    "evt_error",
                    WorkflowConstants.SSE_EVENT_ERROR,
                    operation_id,
                    WorkflowConstants.RUN_STATUS_CRASHED,
                    run_id=run_id,
                    error_message=WorkflowConstants.CANCELLED_BY_SERVER_MESSAGE,
                )
            )

    finally:
        heartbeat_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await heartbeat_task
        await finalize_sse_operation(operation_id)


async def execute_preview_background(
    operation_id: str,
    workflow_def: WorkflowDef,
    sse_queue: asyncio.Queue[object],
    user_id: str,
) -> None:
    """Execute workflow preview in background, pushing SSE events.

    Delegates to ``PreviewWorkflowUseCase`` which runs with ``dry_run=True``.
    No run records are created — previews are ephemeral.
    """
    from src.application.use_cases.workflow_preview import PreviewWorkflowUseCase

    try:
        use_case = PreviewWorkflowUseCase()
        preview_result = await use_case.execute(
            workflow_def, sse_queue=sse_queue, user_id=user_id
        )

        await sse_queue.put(
            build_terminal_event(
                "evt_final",
                WorkflowConstants.SSE_EVENT_PREVIEW_COMPLETE,
                operation_id,
                WorkflowConstants.RUN_STATUS_COMPLETED,
                output_tracks=preview_result.output_tracks,
                total_track_count=preview_result.total_track_count,
                metric_columns=preview_result.metric_columns,
                node_summaries=[
                    {
                        "node_id": s.node_id,
                        "node_type": s.node_type,
                        "track_count": s.track_count,
                        "sample_titles": s.sample_titles,
                    }
                    for s in preview_result.node_summaries
                ],
                duration_ms=preview_result.duration_ms,
            )
        )

    except (CancelledError, Exception) as exc:
        error_msg = (
            WorkflowConstants.CANCELLED_BY_SERVER_MESSAGE
            if isinstance(exc, CancelledError)
            else truncate_error_message(
                str(exc), WorkflowConstants.SSE_ERROR_MAX_LENGTH
            )
        )
        with contextlib.suppress(CancelledError, Exception):
            await sse_queue.put(
                build_terminal_event(
                    "evt_error",
                    WorkflowConstants.SSE_EVENT_ERROR,
                    operation_id,
                    WorkflowConstants.RUN_STATUS_FAILED,
                    error_message=error_msg,
                )
            )

    finally:
        await finalize_sse_operation(operation_id)
