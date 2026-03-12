"""Use cases for workflow execution and run history.

RunWorkflowUseCase creates a PENDING run record synchronously and returns
the run_id so the client can immediately optimistically render the run.

ExecuteWorkflowRunUseCase manages the run lifecycle (RUNNING → COMPLETED/FAILED)
in the application layer, keeping infrastructure concerns out of the route handler.

GetWorkflowRunsUseCase / GetWorkflowRunUseCase serve the run history UI.
"""

# pyright: reportExplicitAny=false
# Legitimate Any: SSE queue carries heterogeneous event dicts, Coroutine params

import asyncio
from asyncio import CancelledError
from datetime import UTC, datetime
from typing import Any

from attrs import define

from src.application.utilities.timing import ExecutionTimer
from src.application.workflows.prefect import (
    WorkflowAlreadyRunningError,
    is_workflow_running,
)
from src.application.workflows.protocols import NodeStatusUpdater, RunStatusUpdater
from src.config.constants import WorkflowConstants
from src.config.logging import get_logger
from src.domain.entities.track import Track
from src.domain.entities.workflow import (
    RunStatus,
    Workflow,
    WorkflowDef,
    WorkflowRun,
    WorkflowRunNode,
)
from src.domain.exceptions import NotFoundError
from src.domain.repositories.interfaces import UnitOfWorkProtocol

logger = get_logger(__name__).bind(service="workflow_runs")


def serialize_output_tracks(
    tracks: list[Track],
    limit: int | None = None,
    metrics: dict[str, dict[int, Any]] | None = None,
) -> tuple[list[dict[str, object]], list[str]]:
    """Serialize result tracks into lightweight dicts for the run record.

    Returns (tracks, metric_columns) where each track dict includes a ``metrics``
    sub-dict keyed by the selected columns. Up to MAX_OUTPUT_METRIC_COLUMNS
    columns are included, sorted alphabetically for deterministic ordering.
    """
    subset = tracks[:limit] if limit is not None else tracks

    # Select and cap metric columns
    if metrics:
        metric_columns = sorted(metrics.keys())[
            : WorkflowConstants.MAX_OUTPUT_METRIC_COLUMNS
        ]
    else:
        metric_columns = []

    result: list[dict[str, object]] = []
    for rank, track in enumerate(subset, 1):
        entry: dict[str, object] = {
            "track_id": track.id or 0,
            "title": track.title or "Unknown",
            "artists": ", ".join(a.name for a in track.artists)
            if track.artists
            else "Unknown",
            "rank": rank,
        }
        if metrics and metric_columns:
            entry["metrics"] = {
                col: metrics[col].get(track.id or 0) for col in metric_columns
            }
        result.append(entry)
    return result, metric_columns


# ---------------------------------------------------------------------------
# Run (create pending run)
# ---------------------------------------------------------------------------


@define(frozen=True, slots=True)
class RunWorkflowCommand:
    workflow_id: int


@define(frozen=True, slots=True)
class RunWorkflowResult:
    run_id: int
    workflow: Workflow


@define(slots=True)
class RunWorkflowUseCase:
    """Creates a PENDING run record with pre-created node records.

    Checks the execution guard first (409 if already running).
    Returns the run_id so the API layer can launch the background task.
    """

    async def execute(
        self, command: RunWorkflowCommand, uow: UnitOfWorkProtocol
    ) -> RunWorkflowResult:
        async with uow:
            repo = uow.get_workflow_repository()
            workflow = await repo.get_workflow_by_id(command.workflow_id)

            # Check execution guard
            if await is_workflow_running(workflow.definition.id):
                raise WorkflowAlreadyRunningError(workflow.definition.id)

            # Build pending run with node records for every task
            definition_snapshot = workflow.definition
            pending_nodes = [
                WorkflowRunNode(
                    node_id=task.id,
                    node_type=task.type,
                    status=WorkflowConstants.RUN_STATUS_PENDING,
                    execution_order=i + 1,
                )
                for i, task in enumerate(definition_snapshot.tasks)
            ]

            run = WorkflowRun(
                workflow_id=workflow.id or 0,
                status=WorkflowConstants.RUN_STATUS_PENDING,
                definition_snapshot=definition_snapshot,
                definition_version=workflow.definition_version,
                nodes=pending_nodes,
            )

            run_repo = uow.get_workflow_run_repository()
            saved_run = await run_repo.create_run(run)

            return RunWorkflowResult(
                run_id=saved_run.id or 0,
                workflow=workflow,
            )


# ---------------------------------------------------------------------------
# List runs
# ---------------------------------------------------------------------------


@define(frozen=True, slots=True)
class ListWorkflowRunsCommand:
    workflow_id: int
    limit: int = 20
    offset: int = 0


@define(frozen=True, slots=True)
class ListWorkflowRunsResult:
    runs: list[WorkflowRun]
    total_count: int


@define(slots=True)
class ListWorkflowRunsUseCase:
    async def execute(
        self, command: ListWorkflowRunsCommand, uow: UnitOfWorkProtocol
    ) -> ListWorkflowRunsResult:
        async with uow:
            # Verify workflow exists
            wf_repo = uow.get_workflow_repository()
            await wf_repo.get_workflow_by_id(command.workflow_id)

            run_repo = uow.get_workflow_run_repository()
            runs, total = await run_repo.get_runs_for_workflow(
                command.workflow_id,
                limit=command.limit,
                offset=command.offset,
            )
            return ListWorkflowRunsResult(runs=runs, total_count=total)


# ---------------------------------------------------------------------------
# Get run detail
# ---------------------------------------------------------------------------


@define(frozen=True, slots=True)
class GetWorkflowRunCommand:
    workflow_id: int
    run_id: int


@define(frozen=True, slots=True)
class GetWorkflowRunResult:
    run: WorkflowRun


@define(slots=True)
class GetWorkflowRunUseCase:
    async def execute(
        self, command: GetWorkflowRunCommand, uow: UnitOfWorkProtocol
    ) -> GetWorkflowRunResult:
        async with uow:
            # Verify workflow exists
            wf_repo = uow.get_workflow_repository()
            await wf_repo.get_workflow_by_id(command.workflow_id)

            run_repo = uow.get_workflow_run_repository()
            run = await run_repo.get_run_by_id(command.run_id)

            # Verify run belongs to this workflow
            if run.workflow_id != command.workflow_id:
                raise NotFoundError(
                    f"Run {command.run_id} does not belong to workflow {command.workflow_id}"
                )

            return GetWorkflowRunResult(run=run)


# ---------------------------------------------------------------------------
# Get latest runs (batch)
# ---------------------------------------------------------------------------


@define(frozen=True, slots=True)
class GetLatestWorkflowRunsCommand:
    workflow_ids: list[int]


@define(frozen=True, slots=True)
class GetLatestWorkflowRunsResult:
    latest_runs: dict[int, WorkflowRun]


@define(slots=True)
class GetLatestWorkflowRunsUseCase:
    """Batch-fetch latest run for each workflow ID."""

    async def execute(
        self, command: GetLatestWorkflowRunsCommand, uow: UnitOfWorkProtocol
    ) -> GetLatestWorkflowRunsResult:
        async with uow:
            run_repo = uow.get_workflow_run_repository()
            latest = await run_repo.get_latest_runs_for_workflows(command.workflow_ids)
            return GetLatestWorkflowRunsResult(latest_runs=latest)


# ---------------------------------------------------------------------------
# Execute workflow run (background lifecycle)
# ---------------------------------------------------------------------------


@define(frozen=True, slots=True)
class ExecuteWorkflowRunResult:
    """Result of a workflow run execution."""

    status: RunStatus
    run_id: int
    duration_ms: int
    output_track_count: int | None = None
    error_message: str | None = None


@define(slots=True)
class ExecuteWorkflowRunUseCase:
    """Manages the RUNNING → COMPLETED/FAILED lifecycle of a workflow run.

    Receives a ``RunStatusUpdater`` callable via constructor injection so
    the application layer stays free of infrastructure imports. The concrete
    updater (which opens an independent DB session) is wired at the call site.
    """

    update_run_status: RunStatusUpdater
    update_node_status: NodeStatusUpdater

    async def execute(
        self,
        workflow_def: WorkflowDef,
        run_id: int,
        sse_queue: asyncio.Queue[Any] | None = None,
    ) -> ExecuteWorkflowRunResult:
        from src.application.services.progress_manager import get_progress_manager
        from src.application.workflows.observers import RunHistoryObserver
        from src.application.workflows.prefect import run_workflow

        timer = ExecutionTimer()

        with logger.contextualize(
            workflow_id=workflow_def.id,
            workflow_name=workflow_def.name,
            run_id=run_id,
        ):
            try:
                # 1. Update run → RUNNING
                await self.update_run_status(
                    run_id,
                    WorkflowConstants.RUN_STATUS_RUNNING,
                    started_at=datetime.now(UTC),
                )

                # 2. Execute workflow with observer for node-level tracking
                progress_manager = get_progress_manager()
                observer = RunHistoryObserver(
                    run_id=run_id,
                    update_node_status=self.update_node_status,
                    sse_queue=sse_queue,
                )
                result = await run_workflow(
                    workflow_def,
                    progress_manager=progress_manager,
                    observer=observer,
                )

                # 3. Check for observer degradation (DB persistence failures)
                if observer.persist_failure_count > 0:
                    logger.error(
                        "Run history incomplete — DB persistence failures during execution",
                        persist_failures=observer.persist_failure_count,
                    )

                # 4. Update run → COMPLETED
                duration_ms = timer.stop()
                output_track_count = len(result.tracks) if result.tracks else None
                output_tracks, _metric_columns = serialize_output_tracks(
                    result.tracks, metrics=result.metrics
                )

                await self.update_run_status(
                    run_id,
                    WorkflowConstants.RUN_STATUS_COMPLETED,
                    completed_at=datetime.now(UTC),
                    duration_ms=duration_ms,
                    output_track_count=output_track_count,
                    output_tracks=output_tracks,
                )

                return ExecuteWorkflowRunResult(
                    status=WorkflowConstants.RUN_STATUS_COMPLETED,
                    run_id=run_id,
                    duration_ms=duration_ms,
                    output_track_count=output_track_count,
                )

            except CancelledError:
                duration_ms = timer.stop()
                logger.warning(
                    "Workflow background task cancelled (possible server reload)",
                    elapsed_ms=duration_ms,
                )

                try:
                    await self.update_run_status(
                        run_id,
                        WorkflowConstants.RUN_STATUS_FAILED,
                        duration_ms=duration_ms,
                        error_message=WorkflowConstants.CANCELLED_BY_SERVER_MESSAGE,
                    )
                except CancelledError, Exception:
                    logger.opt(exception=True).error(
                        "Failed to update cancelled run status to FAILED"
                    )

                raise  # Re-raise so caller can handle SSE cleanup

            except Exception as exc:
                logger.opt(exception=True).error("Workflow execution failed")

                duration_ms = timer.stop()
                error_msg = str(exc)[: WorkflowConstants.ERROR_MESSAGE_MAX_LENGTH]

                try:
                    await self.update_run_status(
                        run_id,
                        WorkflowConstants.RUN_STATUS_FAILED,
                        duration_ms=duration_ms,
                        error_message=error_msg,
                    )
                except Exception:
                    logger.opt(exception=True).error(
                        "Failed to update run status to FAILED"
                    )

                return ExecuteWorkflowRunResult(
                    status=WorkflowConstants.RUN_STATUS_FAILED,
                    run_id=run_id,
                    duration_ms=duration_ms,
                    error_message=error_msg,
                )
