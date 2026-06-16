"""Use cases for workflow execution and run history.

RunWorkflowUseCase creates a PENDING run record synchronously and returns
the run_id so the client can immediately optimistically render the run.

ExecuteWorkflowRunUseCase manages the run lifecycle (RUNNING → COMPLETED/FAILED)
in the application layer, keeping infrastructure concerns out of the route handler.

GetWorkflowRunsUseCase / GetWorkflowRunUseCase serve the run history UI.
"""

import asyncio
from asyncio import CancelledError
from datetime import UTC, datetime
from uuid import UUID

from attrs import define

from src.application.utilities.timing import ExecutionTimer
from src.application.workflows.protocols import NodeStatusUpdater, RunStatusUpdater
from src.config.constants import (
    BusinessLimits,
    WorkflowConstants,
    truncate_error_message,
)
from src.config.logging import get_logger, logging_context
from src.domain.entities.operations import OperationResult
from src.domain.entities.shared import MetricValue
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


def _jsonable_metric(value: MetricValue) -> int | float | str | None:
    """Convert a ``MetricValue`` to a strict-JSON scalar for in-process callers.

    ``serialize_output_tracks`` is consumed by both the JSONB write path
    (workflow_runs.output_tracks) and the preview API path (no DB round-
    trip). Stringifying at the boundary lets both paths share a single
    JSON-compatible shape.
    """
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def serialize_output_tracks(
    tracks: list[Track],
    limit: int | None = None,
    metrics: dict[str, dict[UUID, MetricValue]] | None = None,
) -> tuple[list[dict[str, object]], list[str]]:
    """Serialize result tracks into lightweight dicts for the run record.

    Returns (tracks, metric_columns) where each track dict includes a ``metrics``
    sub-dict keyed by the selected columns. Up to MAX_OUTPUT_METRIC_COLUMNS
    columns are included, sorted alphabetically for deterministic ordering.

    Values in the returned dicts are strict-JSON types (``str``, ``int``,
    ``float``, ``None``) — ``track.id`` is stringified and any ``datetime``
    metric value is converted to ISO 8601 at the boundary. This is for the
    benefit of in-process consumers (preview API responses, CLI rendering,
    unit-test assertions); orjson handles raw UUID / datetime values at
    the JSONB write path natively (see ``db_connection.set_json_dumps``).
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
            "track_id": str(track.id),
            "title": track.title or "Unknown",
            "artists": track.artists_display or "Unknown",
            "rank": rank,
        }
        if metrics and metric_columns:
            entry["metrics"] = {
                col: _jsonable_metric(metrics[col].get(track.id))
                for col in metric_columns
            }
        result.append(entry)
    return result, metric_columns


# ---------------------------------------------------------------------------
# Run (create pending run)
# ---------------------------------------------------------------------------


@define(frozen=True, slots=True)
class RunWorkflowCommand:
    user_id: str
    workflow_id: UUID
    operation_id: str | None = None
    # Set by the scheduler when this run was fired by a schedule, so the run
    # row traces back to its trigger. None for manual (CLI/API) runs.
    triggered_by_schedule_id: UUID | None = None


@define(frozen=True, slots=True)
class RunWorkflowResult:
    run_id: UUID
    workflow: Workflow


@define(slots=True)
class RunWorkflowUseCase:
    """Creates a PENDING run record with pre-created node records.

    Inserting the pending row enforces the concurrency guard at the DB
    (``uq_workflow_runs_active`` → ``WorkflowAlreadyRunningError`` → 409 if a run
    is already active for this workflow). Returns the run_id so the API layer can
    launch the background task.
    """

    async def execute(
        self, command: RunWorkflowCommand, uow: UnitOfWorkProtocol
    ) -> RunWorkflowResult:
        async with uow:
            repo = uow.get_workflow_repository()
            workflow = await repo.get_workflow_by_id(
                command.workflow_id, user_id=command.user_id
            )

            # Concurrency guard is enforced at the DB: inserting the pending run
            # below trips the uq_workflow_runs_active partial unique index if a
            # run is already active for this workflow, raising
            # WorkflowAlreadyRunningError (→ 409). Keyed on the workflow row id,
            # so it holds across instances in a multi-machine deploy.

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
                workflow_id=workflow.id,
                operation_id=command.operation_id,
                status=WorkflowConstants.RUN_STATUS_PENDING,
                definition_snapshot=definition_snapshot,
                definition_version=workflow.definition_version,
                nodes=pending_nodes,
                triggered_by_schedule_id=command.triggered_by_schedule_id,
            )

            run_repo = uow.get_workflow_run_repository()
            saved_run = await run_repo.create_run(run)

            return RunWorkflowResult(
                run_id=saved_run.id,
                workflow=workflow,
            )


# ---------------------------------------------------------------------------
# List runs
# ---------------------------------------------------------------------------


@define(frozen=True, slots=True)
class ListWorkflowRunsCommand:
    user_id: str
    workflow_id: UUID
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
            # Verify workflow exists and user owns it
            wf_repo = uow.get_workflow_repository()
            await wf_repo.get_workflow_by_id(
                command.workflow_id, user_id=command.user_id
            )

            run_repo = uow.get_workflow_run_repository()
            runs, total = await run_repo.get_runs_for_workflow(
                command.workflow_id,
                limit=command.limit,
                offset=command.offset,
            )
            return ListWorkflowRunsResult(runs=runs, total_count=total)


# ---------------------------------------------------------------------------
# List active runs (cross-workflow)
# ---------------------------------------------------------------------------


@define(frozen=True, slots=True)
class ListActiveRunsCommand:
    user_id: str
    limit: int = 50
    offset: int = 0


@define(frozen=True, slots=True)
class ListActiveRunsResult:
    runs: list[WorkflowRun]
    total_count: int


@define(slots=True)
class ListActiveRunsUseCase:
    """List the user's in-flight runs across every workflow.

    The app-global "what's running now" source. Reads cross-instance truth from
    the DB so the detail page can reconnect to a live run after reload and a
    future sidebar can light up without per-workflow polling. User scoping lives
    in the repository JOIN — no per-workflow ownership check needed here.
    """

    async def execute(
        self, command: ListActiveRunsCommand, uow: UnitOfWorkProtocol
    ) -> ListActiveRunsResult:
        async with uow:
            run_repo = uow.get_workflow_run_repository()
            runs, total = await run_repo.get_active_runs_for_user(
                command.user_id,
                limit=command.limit,
                offset=command.offset,
            )
            return ListActiveRunsResult(runs=runs, total_count=total)


# ---------------------------------------------------------------------------
# Get run detail
# ---------------------------------------------------------------------------


@define(frozen=True, slots=True)
class GetWorkflowRunCommand:
    user_id: str
    workflow_id: UUID
    run_id: UUID


@define(frozen=True, slots=True)
class GetWorkflowRunResult:
    run: WorkflowRun


@define(slots=True)
class GetWorkflowRunUseCase:
    async def execute(
        self, command: GetWorkflowRunCommand, uow: UnitOfWorkProtocol
    ) -> GetWorkflowRunResult:
        async with uow:
            # Verify workflow exists and user owns it
            wf_repo = uow.get_workflow_repository()
            await wf_repo.get_workflow_by_id(
                command.workflow_id, user_id=command.user_id
            )

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
    user_id: str
    workflow_ids: list[UUID]


@define(frozen=True, slots=True)
class GetLatestWorkflowRunsResult:
    latest_runs: dict[UUID, WorkflowRun]


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
    run_id: UUID
    duration_ms: int
    output_track_count: int | None = None
    error_message: str | None = None
    # The executor's full result (tracks + metrics), present only on the success
    # path. Lets the CLI render its track table from one unified return value
    # without a second query — the API path ignores it (it streams via SSE).
    operation_result: OperationResult | None = None


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
        run_id: UUID,
        sse_queue: asyncio.Queue[object] | None = None,
        user_id: str = BusinessLimits.DEFAULT_USER_ID,
    ) -> ExecuteWorkflowRunResult:
        from src.application.workflows.engine.executor import WorkflowCancelledError

        timer = ExecutionTimer()

        with logging_context(
            workflow_id=workflow_def.id,
            workflow_name=workflow_def.name,
            run_id=run_id,
        ):
            try:
                return await self._run_to_completion(
                    workflow_def,
                    run_id,
                    sse_queue,
                    user_id,
                    timer,
                )

            except CancelledError:
                duration_ms = timer.stop()
                logger.warning(
                    "Workflow background task cancelled (possible server reload)",
                    elapsed_ms=duration_ms,
                )

                # Worker died out from under the run (SIGTERM/reload), not a
                # logic failure — record CRASHED so the user can tell an
                # operational event apart from a broken pipeline.
                try:
                    await self.update_run_status(
                        run_id,
                        WorkflowConstants.RUN_STATUS_CRASHED,
                        duration_ms=duration_ms,
                        error_message=WorkflowConstants.CANCELLED_BY_SERVER_MESSAGE,
                    )
                except CancelledError, Exception:
                    logger.error(
                        "Failed to update cancelled run status to CRASHED",
                        exc_info=True,
                    )

                raise  # Re-raise so caller can handle SSE cleanup

            except WorkflowCancelledError as exc:
                # Cooperative graceful shutdown between nodes (e.g. SIGTERM drain
                # on deploy/autoscale). An orderly operational stop, not a broken
                # pipeline — record CANCELLED so users (and the sweeper) can tell
                # it apart from `failed`/`crashed`.
                duration_ms = timer.stop()
                error_msg = truncate_error_message(
                    str(exc), WorkflowConstants.ERROR_MESSAGE_MAX_LENGTH
                )
                try:
                    await self.update_run_status(
                        run_id,
                        WorkflowConstants.RUN_STATUS_CANCELLED,
                        completed_at=datetime.now(UTC),
                        duration_ms=duration_ms,
                        error_message=error_msg,
                    )
                except Exception:
                    logger.error(
                        "Failed to update run status to CANCELLED",
                        exc_info=True,
                    )
                return ExecuteWorkflowRunResult(
                    status=WorkflowConstants.RUN_STATUS_CANCELLED,
                    run_id=run_id,
                    duration_ms=duration_ms,
                    error_message=error_msg,
                )

            except Exception as exc:
                logger.error("Workflow execution failed", exc_info=True)

                duration_ms = timer.stop()
                error_msg = truncate_error_message(
                    str(exc), WorkflowConstants.ERROR_MESSAGE_MAX_LENGTH
                )

                try:
                    await self.update_run_status(
                        run_id,
                        WorkflowConstants.RUN_STATUS_FAILED,
                        duration_ms=duration_ms,
                        error_message=error_msg,
                    )
                except Exception:
                    logger.error(
                        "Failed to update run status to FAILED",
                        exc_info=True,
                    )

                return ExecuteWorkflowRunResult(
                    status=WorkflowConstants.RUN_STATUS_FAILED,
                    run_id=run_id,
                    duration_ms=duration_ms,
                    error_message=error_msg,
                )

    async def _run_to_completion(
        self,
        workflow_def: WorkflowDef,
        run_id: UUID,
        sse_queue: asyncio.Queue[object] | None,
        user_id: str,
        timer: ExecutionTimer,
    ) -> ExecuteWorkflowRunResult:
        """Drive the run from RUNNING through COMPLETED and build the result.

        Extracted from ``execute`` so the protective ``try`` clause stays small;
        the same statements remain guarded by the caller's lifecycle ``except``
        clauses (``CancelledError`` / ``WorkflowCancelledError`` / ``Exception``).
        """
        from src.application.services.progress_broker import get_progress_broker
        from src.application.workflows.engine.executor import run_workflow
        from src.application.workflows.engine.observers import RunHistoryObserver

        # 1. Update run → RUNNING
        await self.update_run_status(
            run_id,
            WorkflowConstants.RUN_STATUS_RUNNING,
            started_at=datetime.now(UTC),
        )

        # 2. Execute workflow with observer for node-level tracking
        progress_broker = get_progress_broker()
        observer = RunHistoryObserver(
            run_id=run_id,
            update_node_status=self.update_node_status,
            sse_queue=sse_queue,
        )
        logger.info("Calling run_workflow", run_id=str(run_id))
        result = await run_workflow(
            workflow_def,
            progress_broker=progress_broker,
            observer=observer,
            user_id=user_id,
        )
        logger.info("run_workflow returned", run_id=str(run_id))

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
            operation_result=result,
        )
