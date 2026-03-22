"""Executes music playlist workflows using the Prefect v3 orchestration engine.

Converts declarative workflow definitions (JSON configs) into executable Prefect flows.
Handles playlist transformations like fetching tracks, applying filters, enriching metadata,
and creating/updating playlists across music platforms. Provides progress tracking, error
recovery, and database session management for long-running playlist operations.
"""

# pyright: reportExplicitAny=false, reportAny=false

import asyncio
import datetime
import signal
import time
from typing import Any

import attrs
from prefect import flow, tags, task
from prefect.cache_policies import NONE
from prefect.logging import get_run_logger

# Use Mixd's standard logger for module-level logging
from src.application.services.progress_manager import AsyncProgressManager
from src.config.constants import NodeType, WorkflowConstants
from src.config.logging import get_logger
from src.domain.entities.operations import OperationResult
from src.domain.entities.progress import (
    OperationStatus,
    create_progress_operation,
)
from src.domain.entities.workflow import (
    NodeExecutionEvent,
    NodeExecutionRecord,
    WorkflowDef,
    WorkflowTaskDef,
)

from .node_registry import get_node
from .observers import NullNodeObserver, ProgressNodeObserver
from .protocols import NodeExecutionObserver, NodeResult
from .validation import (
    ConnectorNotAvailableError,
    extract_required_connectors,
    validate_connector_availability,
    validate_workflow_def,
)

logger = get_logger(__name__)

# One-time registry validation guard — runs before first workflow execution
_registry_validated = False

# --- Execution guard (conflict detection) ---

_running_workflows: set[str] = set()
_running_lock = asyncio.Lock()


async def is_workflow_running(workflow_id: str) -> bool:
    """Check if a workflow is currently executing (for v0.4.1 409 Conflict)."""
    async with _running_lock:
        return workflow_id in _running_workflows


class WorkflowAlreadyRunningError(Exception):
    """Raised when attempting to execute a workflow that is already running."""

    def __init__(self, workflow_id: str) -> None:
        self.workflow_id = workflow_id
        super().__init__(f"Workflow '{workflow_id}' is already running")


# --- Fault tolerance ---


# Categories where a node failure degrades rather than kills the workflow.
# Enricher failures are recoverable: downstream nodes use cached/stale metrics.
_RECOVERABLE_CATEGORIES: frozenset[str] = frozenset({"enricher"})


def _is_failure_recoverable(node_type: str) -> bool:
    """Check if a node failure should degrade rather than kill the workflow.

    Enricher failures are recoverable because:
    - The upstream tracklist can pass through unchanged
    - Cached metrics from previous runs may still be available
    - Missing metrics cause downstream filters to drop tracks (safe degradation)
      rather than producing incorrect results

    Source/transform/destination failures remain fatal.
    """
    category = node_type.split(".", maxsplit=1)[0]
    return category in _RECOVERABLE_CATEGORIES


class WorkflowCancelledError(Exception):
    """Raised when a workflow is cancelled by a graceful shutdown signal."""


# Graceful shutdown: set between nodes so the current node completes
_shutdown_requested = False


def _request_shutdown() -> None:
    """Signal handler callback — sets the shutdown flag for the orchestration loop."""
    global _shutdown_requested
    _shutdown_requested = True
    logger.warning(
        "Graceful shutdown requested — will stop after current node completes"
    )


# --- Node timeout ---


_CATEGORY_TIMEOUTS: dict[NodeType, int] = {
    "source": WorkflowConstants.SOURCE_TIMEOUT_SECONDS,
    "enricher": WorkflowConstants.ENRICHER_TIMEOUT_SECONDS,
    "destination": WorkflowConstants.DESTINATION_TIMEOUT_SECONDS,
    "filter": WorkflowConstants.TRANSFORM_TIMEOUT_SECONDS,
    "sorter": WorkflowConstants.TRANSFORM_TIMEOUT_SECONDS,
    "selector": WorkflowConstants.TRANSFORM_TIMEOUT_SECONDS,
}


def _get_node_timeout(node_type: str) -> int:
    """Return asyncio.timeout budget (seconds) for a node category.

    Falls back to TRANSFORM_TIMEOUT_SECONDS for unknown categories.
    """
    category: NodeType = node_type.split(".", maxsplit=1)[0]  # type: ignore[assignment]  # runtime string from workflow def
    return _CATEGORY_TIMEOUTS.get(category, WorkflowConstants.TRANSFORM_TIMEOUT_SECONDS)


# --- Node execution ---


@task(
    tags=["node"],
    cache_policy=NONE,  # Disable caching due to non-serializable context objects
    task_run_name="execute-{node_type}",
    # No retries — source/enricher nodes retry via infrastructure tenacity policies;
    # transform nodes are pure and deterministic (retrying won't help)
)
async def execute_node(
    node_type: str, context: dict[str, Any], config: dict[str, Any]
) -> NodeResult:
    """Execute a single workflow node.

    Failure logging and progress tracking are handled by the observer in
    ``build_flow``'s execution loop — this function is intentionally thin.
    """
    node_func, _ = get_node(node_type)
    enhanced_context = context.copy()
    enhanced_context.update({"node_type": node_type})
    return await node_func(enhanced_context, config)


# --- Flow building ---


def generate_flow_run_name(flow_name: str) -> str:
    """Creates timestamped flow run name for Prefect UI identification."""
    return (
        f"{flow_name}-{datetime.datetime.now(datetime.UTC).strftime('%Y%m%d-%H%M%S')}"
    )


def _get_input_track_count(
    task_def: Any, task_results: dict[str, NodeResult]
) -> int | None:
    """Extract track count from upstream result, if available."""
    if not task_def.upstream:
        return None
    upstream_id = task_def.upstream[0]
    upstream_result = task_results.get(upstream_id)
    if upstream_result:
        return len(upstream_result["tracklist"].tracks)
    return None


def build_flow(
    workflow_def: WorkflowDef,
    observer: NodeExecutionObserver | None = None,
    dry_run: bool = False,
) -> Any:
    """Converts typed workflow definition into executable Prefect flow function.

    Computes parallel execution levels from the task DAG, then executes each
    level concurrently via ``asyncio.gather()``. Tasks within a level are
    independent by definition — their dependencies are all in prior levels.

    Uses the official Prefect 3 pattern for async concurrency (asyncio.gather)
    rather than ThreadPoolTaskRunner.submit(), which creates separate event
    loops per thread and breaks asyncio.Queue-based SSE observers.

    Args:
        workflow_def: Typed workflow definition with tasks, dependencies, and config.
        observer: Optional lifecycle observer for node start/complete/fail events.
        dry_run: When True, destination nodes skip external writes.

    Returns:
        Async Prefect flow function ready for execution.
    """
    from .validation import compute_parallel_levels

    node_observer = observer or NullNodeObserver()

    # Extract workflow metadata
    flow_name = workflow_def.name
    flow_description = workflow_def.description

    # Compute parallel execution levels from the DAG
    levels = compute_parallel_levels(workflow_def.tasks)

    # Flat execution_order mapping (1-based) for progress reporting
    flat_order: dict[str, int] = {}
    order_counter = 1
    for level in levels:
        for task_def in level:
            flat_order[task_def.id] = order_counter
            order_counter += 1

    total_nodes = sum(len(level) for level in levels)

    @flow(
        name=flow_name,
        description=flow_description,
        flow_run_name=generate_flow_run_name(flow_name),
    )
    async def workflow_flow(
        workflow_progress_manager: AsyncProgressManager | None = None,
        workflow_operation_id: str | None = None,
        **parameters: object,
    ) -> dict[str, Any]:
        """Executes workflow tasks level-by-level with concurrent independent nodes."""
        flow_logger = get_run_logger()
        flow_logger.info("Starting workflow")

        parameters["workflow_name"] = flow_name

        from .context import create_workflow_context

        # Each task creates its own session from the PostgreSQL pool — no
        # shared session needed under MVCC.
        workflow_context = create_workflow_context()

        task_results: dict[str, NodeResult] = {}
        node_records: list[NodeExecutionRecord] = []

        async def _run_node_lifecycle(
            task_def: WorkflowTaskDef,
        ) -> tuple[str, NodeResult | Exception, bool]:
            """Execute one node with full lifecycle management.

            Wraps observer notification, timeout, error handling, and
            diagnostics. Never raises — returns a status tuple so
            ``asyncio.gather`` can run all tasks in a level even if one fails.

            Returns:
                (task_id, result_or_exception, was_fatal_error)
            """
            task_id = task_def.id
            node_type = task_def.type
            execution_order = flat_order[task_id]
            config = task_def.config

            # Graceful shutdown check (cooperative — checked at task start)
            if _shutdown_requested:
                node_records.append(
                    NodeExecutionRecord(
                        node_id=task_id,
                        node_type=node_type,
                        execution_order=execution_order,
                        status="skipped",
                        error_message="Cancelled by graceful shutdown",
                    )
                )
                return (task_id, WorkflowCancelledError("Shutdown requested"), True)

            flow_logger.info(f"Starting task: {task_id} (type: {node_type})")

            # Build task-specific context from static metadata + upstream results.
            # Avoids copying the entire context bag (which grows with each completed
            # node) — only includes what this task actually needs.
            task_context: dict[str, Any] = {
                "parameters": parameters,
                "workflow_context": workflow_context,
                "workflow_name": flow_name,
                "progress_manager": workflow_progress_manager,
                "workflow_operation_id": workflow_operation_id,
                "total_tasks": total_nodes,
                "dry_run": dry_run,
                "current_step": execution_order,
            }

            if task_def.upstream:
                if len(task_def.upstream) == 1:
                    task_context["upstream_task_id"] = task_def.upstream[0]
                else:
                    primary_input = config.get("primary_input")
                    if primary_input and primary_input in task_def.upstream:
                        task_context["upstream_task_id"] = primary_input
                    else:
                        task_context["upstream_task_id"] = task_def.upstream[0]

                task_context["upstream_task_ids"] = task_def.upstream

                for upstream_id in task_def.upstream:
                    if upstream_id in task_results:
                        task_context[upstream_id] = task_results[upstream_id]

            # Notify observer and time execution
            input_track_count = _get_input_track_count(task_def, task_results)
            base_event = NodeExecutionEvent(
                task_def=task_def,
                execution_order=execution_order,
                total_nodes=total_nodes,
                input_track_count=input_track_count,
            )
            await node_observer.on_node_starting(base_event)
            timeout_seconds = _get_node_timeout(node_type)
            start_ns = time.perf_counter_ns()

            was_degraded = False
            try:
                async with asyncio.timeout(timeout_seconds):
                    result = await execute_node(node_type, task_context, config)
            except (TimeoutError, Exception) as exc:
                duration_ms = (time.perf_counter_ns() - start_ns) // 1_000_000

                if isinstance(exc, TimeoutError):
                    exc = TimeoutError(
                        f"Node '{task_id}' ({node_type}) exceeded "
                        f"{timeout_seconds}s timeout"
                    )

                logger.opt(exception=True).error(
                    "Node execution failed",
                    node_id=task_id,
                    node_type=node_type,
                    execution_order=execution_order,
                    total_nodes=total_nodes,
                    duration_ms=duration_ms,
                )
                failed_event = attrs.evolve(base_event, duration_ms=duration_ms)
                await node_observer.on_node_failed(failed_event, exc)

                # Fault tolerance: enricher failures degrade rather than kill
                if (
                    _is_failure_recoverable(node_type)
                    and task_def.upstream
                    and task_def.upstream[0] in task_results
                ):
                    upstream_result = task_results[task_def.upstream[0]]
                    result = upstream_result  # pass through upstream tracklist
                    node_records.append(
                        failed_event.to_record(
                            status="degraded",
                            error_message=str(exc),
                        )
                    )
                    was_degraded = True
                    logger.warning(
                        "Node degraded — continuing with upstream data",
                        node_id=task_id,
                        node_type=node_type,
                        error=str(exc),
                    )
                    # Fall through to success path (store result)
                else:
                    node_records.append(
                        failed_event.to_record(status="failed", error_message=str(exc))
                    )
                    return (task_id, exc, True)
            else:
                duration_ms = (time.perf_counter_ns() - start_ns) // 1_000_000

            output_track_count = len(result["tracklist"].tracks)

            # Structured track-count checkpoint for diagnostics
            input_count = input_track_count or 0
            delta = input_count - output_track_count
            logger.info(
                "track_count_checkpoint",
                node_id=task_id,
                node_type=node_type,
                input_count=input_count,
                output_count=output_track_count,
                delta=delta,
            )
            if delta > 0:
                dropped_ids: list[int | None] = []
                if input_track_count and task_def.upstream:
                    upstream_res = task_results.get(task_def.upstream[0])
                    if upstream_res:
                        output_ids = {t.id for t in result["tracklist"].tracks}
                        dropped_ids = [
                            t.id
                            for t in upstream_res["tracklist"].tracks
                            if t.id not in output_ids
                        ]
                logger.debug(
                    "track_count_dropped_ids",
                    node_id=task_id,
                    dropped_track_ids=dropped_ids,
                )

            # Emit completed event (degraded nodes already had on_node_failed)
            if not was_degraded:
                completed_event = attrs.evolve(
                    base_event,
                    duration_ms=duration_ms,
                    output_track_count=output_track_count,
                )
                await node_observer.on_node_completed(completed_event, result)
                node_records.append(completed_event.to_record(status="completed"))

            # Store result key alias so downstream nodes can reference it
            if task_def.result_key:
                flow_logger.debug(f"Storing result under key: {task_def.result_key}")
                task_results[task_def.result_key] = result

            return (task_id, result, False)

        # --- Level-based execution ---
        try:
            completed_count = 0
            for level in levels:
                # Graceful shutdown: check between levels
                if _shutdown_requested:
                    remaining_tasks = [
                        td for lvl in levels for td in lvl if td.id not in task_results
                    ]
                    logger.warning(
                        "Shutdown requested — cancelling remaining nodes",
                        completed_nodes=completed_count,
                        remaining_nodes=len(remaining_tasks),
                    )
                    node_records.extend(
                        NodeExecutionRecord(
                            node_id=remaining.id,
                            node_type=remaining.type,
                            execution_order=flat_order[remaining.id],
                            status="skipped",
                            error_message="Cancelled by graceful shutdown",
                        )
                        for remaining in remaining_tasks
                    )
                    raise WorkflowCancelledError(
                        f"Shutdown after {completed_count}/{total_nodes} nodes"
                    )

                if len(level) == 1:
                    # Single task — execute directly (avoid gather overhead)
                    task_id, result_or_exc, fatal = await _run_node_lifecycle(level[0])
                    if fatal:
                        raise result_or_exc  # type: ignore[misc]
                    task_results[task_id] = result_or_exc  # type: ignore[assignment]
                    completed_count += 1
                else:
                    # Multiple independent tasks — run concurrently
                    outcomes = await asyncio.gather(
                        *[_run_node_lifecycle(td) for td in level],
                    )
                    # Process outcomes: store results, check for fatal failures
                    fatal_error: Exception | None = None
                    for task_id, result_or_exc, fatal in outcomes:
                        if fatal:
                            if fatal_error is None:
                                fatal_error = result_or_exc  # type: ignore[assignment]
                        else:
                            task_results[task_id] = result_or_exc  # type: ignore[assignment]
                            completed_count += 1
                    if fatal_error is not None:
                        raise fatal_error

            flow_logger.info("Workflow completed successfully")

            return {
                "_task_results": task_results,
                "_node_records": node_records,
            }
        finally:
            # Close cached connector instances (httpx pools) on success or failure
            await workflow_context.connectors.aclose()

    # Return the decorated flow function
    return workflow_flow


# --- Workflow execution ---


def _aggregate_workflow_metrics(
    task_results: dict[str, NodeResult],
) -> dict[str, dict[int, Any]]:
    """Aggregate metrics from all workflow task results.

    Iterates through task results, extracting metrics from each tracklist's
    metadata and merging them into a unified dict.
    """
    all_metrics: dict[str, dict[int, Any]] = {}

    for task_id, result in task_results.items():
        tracklist = result["tracklist"]
        task_metrics: dict[str, dict[int, Any]] = tracklist.metadata.get("metrics", {})

        for metric_name, values in task_metrics.items():
            if values:
                logger.debug(
                    f"Metrics found in {task_id}",
                    metric_name=metric_name,
                    key_count=len(values),
                )

            if metric_name not in all_metrics:
                all_metrics[metric_name] = {}
            all_metrics[metric_name].update(values.copy())

    logger.debug(
        "Aggregated workflow metrics",
        metric_names=list(all_metrics.keys()),
    )
    return all_metrics


def extract_workflow_result(
    workflow_def: WorkflowDef,
    task_results: dict[str, NodeResult],
    execution_time: float,
) -> OperationResult:
    """Extracts final tracklist and aggregates metrics from all workflow tasks.

    Finds the destination task (playlist creation/update) to get the final filtered
    tracklist, then combines metrics from all intermediate tasks (play counts,
    listener counts, etc.) into a comprehensive result structure.

    Args:
        workflow_def: Original workflow definition
        task_results: Results from all executed tasks
        execution_time: Total workflow execution time in seconds

    Returns:
        Structured result with final tracks, aggregated metrics, and timing info
    """

    # Find the destination task - it should be the last one in the workflow
    destination_task = next(
        (t for t in reversed(workflow_def.tasks) if t.type.startswith("destination.")),
        None,
    )

    if not destination_task:
        raise ValueError("No destination task found in workflow")

    destination_id = destination_task.id

    if destination_id not in task_results:
        raise ValueError(f"Destination task result not found: {destination_id}")

    # Get the tracklist from the destination result - this is the FINAL filtered list
    destination_result = task_results[destination_id]
    if "tracklist" not in destination_result:
        raise ValueError(f"Destination task has no tracklist: {destination_id}")

    # Use the FINAL filtered tracks from destination
    final_tracklist = destination_result["tracklist"]
    final_tracks = final_tracklist.tracks

    all_metrics = _aggregate_workflow_metrics(task_results)

    return OperationResult(
        tracks=final_tracks,
        metrics=all_metrics,
        operation_name=workflow_def.name,
        execution_time=execution_time,
        tracklist=final_tracklist,
    )


@flow(name="run_workflow")
async def run_workflow(
    workflow_def: WorkflowDef,
    progress_manager: AsyncProgressManager | None = None,
    observer: object | None = None,
    dry_run: bool = False,
    **parameters: object,
) -> OperationResult:
    """Executes complete playlist workflow from JSON definition to final result.

    Main entry point for workflow execution. Builds Prefect flow from definition,
    executes all tasks with proper dependency ordering, times execution, and
    extracts final results with aggregated metrics.

    Args:
        workflow_def: Typed workflow definition with tasks and dependencies.
        progress_manager: Optional AsyncProgressManager for CLI progress tracking.
        observer: Optional NodeExecutionObserver for node lifecycle events.
            Typed as ``object`` because Prefect validates flow params via Pydantic
            which rejects Protocol types. When progress_manager is provided and
            no explicit observer, a ProgressNodeObserver is created automatically.
        **parameters: Dynamic parameters passed to workflow tasks.

    Returns:
        Structured operation result with final tracks and aggregated metrics.
    """

    global _registry_validated
    if not _registry_validated:
        from .registry_validation import validate_registry

        validate_registry()
        _registry_validated = True

    validate_workflow_def(workflow_def)

    # Pre-flight connector validation — fail fast before any I/O
    required_connectors = extract_required_connectors(workflow_def)
    if required_connectors:
        from .context import ConnectorRegistryImpl

        available = ConnectorRegistryImpl().list_connectors()
        missing = validate_connector_availability(required_connectors, available)
        if missing:
            raise ConnectorNotAvailableError(missing)

    # Execution guard — prevent concurrent runs of the same workflow
    workflow_id = workflow_def.id
    async with _running_lock:
        if workflow_id in _running_workflows:
            raise WorkflowAlreadyRunningError(workflow_id)
        _running_workflows.add(workflow_id)

    flow_logger = get_run_logger()
    workflow_name = workflow_def.name

    # Generate a unique run ID for structured logging correlation
    import uuid

    from src.config.logging import (
        add_workflow_run_logger,
        remove_workflow_run_logger,
    )

    workflow_run_id = str(uuid.uuid4())[:8]
    run_sink_id = add_workflow_run_logger(workflow_def.id, workflow_run_id)

    try:
        with logger.contextualize(
            workflow_id=workflow_def.id,
            workflow_name=workflow_name,
            workflow_run_id=workflow_run_id,
        ):
            # Initialize workflow-level progress tracking
            workflow_operation_id = None
            if progress_manager:
                total_tasks = len(workflow_def.tasks)

                workflow_operation = create_progress_operation(
                    description=f"Executing {workflow_name}", total_items=total_tasks
                )
                workflow_operation_id = await progress_manager.start_operation(
                    workflow_operation
                )
                flow_logger.info(
                    f"Starting workflow execution: {workflow_name} ({total_tasks} tasks)"
                )

            # Compose observers: always add ProgressNodeObserver when progress_manager
            # is active, even if an explicit observer (e.g. RunHistoryObserver) is provided.
            # This enables CLI to get both Rich progress bars AND DB run history.
            from .observers import CompositeNodeObserver

            typed_observers: list[NodeExecutionObserver] = []
            if observer is not None:
                typed_observers.append(observer)  # type: ignore[arg-type]  # Prefect boundary uses object; narrowed here
            if progress_manager and workflow_operation_id:
                typed_observers.append(
                    ProgressNodeObserver(progress_manager, workflow_operation_id)
                )

            effective_observer: NodeExecutionObserver | None
            if len(typed_observers) > 1:
                effective_observer = CompositeNodeObserver(typed_observers)
            elif typed_observers:
                effective_observer = typed_observers[0]
            else:
                effective_observer = None

            # Register SIGTERM handler for graceful shutdown between nodes
            global _shutdown_requested
            _shutdown_requested = False  # reset for each run
            loop = asyncio.get_running_loop()
            sigterm_registered = False
            try:
                loop.add_signal_handler(signal.SIGTERM, _request_shutdown)
                sigterm_registered = True
            except NotImplementedError, OSError:
                # Windows or non-main thread — signal handlers unavailable
                pass

            try:
                with tags("workflow", workflow_name):
                    # Start timing
                    start_time = datetime.datetime.now(datetime.UTC)

                    # Build and execute the workflow
                    workflow = build_flow(
                        workflow_def, observer=effective_observer, dry_run=dry_run
                    )
                    context = await workflow(
                        workflow_progress_manager=progress_manager,
                        workflow_operation_id=workflow_operation_id,
                        **parameters,
                    )

                    # Calculate execution time
                    end_time = datetime.datetime.now(datetime.UTC)
                    execution_time = (end_time - start_time).total_seconds()

                    # Extract typed task results from context
                    task_results: dict[str, NodeResult] = context.pop(
                        "_task_results", {}
                    )

                    # Extract result with actual execution time
                    result = extract_workflow_result(
                        workflow_def,
                        task_results,
                        execution_time,
                    )

                    # Complete workflow-level progress tracking
                    if progress_manager and workflow_operation_id:
                        await progress_manager.complete_operation(
                            workflow_operation_id, OperationStatus.COMPLETED
                        )

                    return result
            except Exception:
                # Mark workflow progress as failed
                if progress_manager and workflow_operation_id:
                    await progress_manager.complete_operation(
                        workflow_operation_id, OperationStatus.FAILED
                    )

                logger.error("Workflow failed — see node error above")
                raise
            finally:
                if sigterm_registered:
                    loop.remove_signal_handler(signal.SIGTERM)
    finally:
        remove_workflow_run_logger(run_sink_id)
        async with _running_lock:
            _running_workflows.discard(workflow_id)
