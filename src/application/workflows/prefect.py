"""Executes music playlist workflows using the Prefect v3 orchestration engine.

Converts declarative workflow definitions (JSON configs) into executable Prefect flows.
Handles playlist transformations like fetching tracks, applying filters, enriching metadata,
and creating/updating playlists across music platforms. Provides progress tracking, error
recovery, and database session management for long-running playlist operations.
"""

# pyright: reportExplicitAny=false, reportAny=false

import datetime
import time
from typing import Any

from prefect import flow, tags, task
from prefect.cache_policies import NONE
from prefect.logging import get_run_logger

# Use Narada's standard logger for module-level logging; Prefect tasks use get_run_logger()
from src.application.services.progress_manager import AsyncProgressManager
from src.config.logging import get_logger
from src.domain.entities.operations import OperationResult
from src.domain.entities.progress import (
    OperationStatus,
    create_progress_operation,
)
from src.domain.entities.workflow import WorkflowDef

from .node_registry import get_node
from .observers import ProgressNodeObserver
from .protocols import NodeExecutionObserver, NodeResult, NullNodeObserver
from .validation import topological_sort, validate_workflow_def

logger = get_logger(__name__)

# One-time registry validation guard — runs before first workflow execution
_registry_validated = False

# --- Progress tracking integration ---

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
    """Executes a single workflow node with Rich CLI progress tracking.

    Wraps node execution with Prefect's retry logic, logging, and automatic progress
    event emission. Each node automatically shows progress bars without requiring
    individual node modifications.

    Args:
        node_type: Node identifier (e.g., "source.playlist", "enricher.spotify")
        context: Shared workflow context including database session and upstream results
        config: Node-specific configuration parameters

    Returns:
        Node execution result (typically containing processed tracklist)
    """
    # Use Prefect's run logger to get task context
    task_logger = get_run_logger()
    task_logger.info(f"Executing node: {node_type}")

    # Get node implementation
    node_func, _ = get_node(node_type)

    try:
        # Add progress metadata to node context
        enhanced_context = context.copy()
        enhanced_context.update({
            "node_type": node_type,
            "task_logger": task_logger,  # Allow nodes to log to Prefect
        })

        # Execute node
        result = await node_func(enhanced_context, config)

        # Progress tracking now handled by NodeExecutionObserver in build_flow()
        task_logger.info(f"Node completed successfully: {node_type}")

    except Exception:
        task_logger.exception(f"Node failed (type: {node_type})")
        raise
    else:
        # All node implementations return {"tracklist": TrackList, ...} — node contract
        return result


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
) -> Any:
    """Converts typed workflow definition into executable Prefect flow function.

    Performs topological sort of tasks based on dependencies, creates shared database
    session, and builds dynamic flow that executes nodes in correct order while
    passing results between dependent tasks.

    Args:
        workflow_def: Typed workflow definition with tasks, dependencies, and config.
        observer: Optional lifecycle observer for node start/complete/fail events.

    Returns:
        Async Prefect flow function ready for execution.
    """
    node_observer = observer or NullNodeObserver()

    # Extract workflow metadata
    flow_name = workflow_def.name
    flow_description = workflow_def.description

    # Sort tasks in execution order
    sorted_tasks = topological_sort(workflow_def.tasks)

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
        """Executes workflow tasks in dependency order with shared database session."""
        # Use Prefect's run logger to get flow context
        flow_logger = get_run_logger()
        flow_logger.info("Starting workflow")

        # Set workflow name in context for progress tracking
        parameters["workflow_name"] = flow_name

        # Create workflow context with all required providers
        from src.infrastructure.persistence.database.db_connection import get_session

        from .context import create_workflow_context

        # Create a single shared session for the entire workflow execution
        async with get_session() as shared_session:
            # Create workflow context with shared session
            workflow_context = create_workflow_context(shared_session)

            # Initialize execution context — heterogeneous bag accumulating
            # task results, upstream IDs, and progress metadata during execution
            total_nodes = len(sorted_tasks)
            context: dict[str, Any] = {
                "parameters": parameters,
                "workflow_context": workflow_context,
                "workflow_name": flow_name,
                "progress_manager": workflow_progress_manager,
                "workflow_operation_id": workflow_operation_id,
                "total_tasks": total_nodes,
            }
            task_results: dict[str, NodeResult] = {}

            try:
                # Execute tasks in dependency order
                for task_index, task_def in enumerate(sorted_tasks):
                    task_id = task_def.id
                    node_type = task_def.type
                    execution_order = task_index + 1  # 1-based

                    # Log the task start
                    flow_logger.info(f"Starting task: {task_id} (type: {node_type})")

                    # Resolve configuration with current context
                    config = task_def.config

                    # Create task-specific context with upstream results and progress tracking
                    task_context = context.copy()
                    task_context["current_step"] = execution_order

                    if task_def.upstream:
                        if len(task_def.upstream) == 1:
                            # Single upstream case
                            task_context["upstream_task_id"] = task_def.upstream[0]
                        else:
                            # Multiple upstream case - first one is primary by convention
                            # (unless config specifies a primary_input)
                            primary_input = config.get("primary_input")
                            if primary_input and primary_input in task_def.upstream:
                                task_context["upstream_task_id"] = primary_input
                            else:
                                task_context["upstream_task_id"] = task_def.upstream[0]

                        # Add all upstream tasks as a list for nodes that need multiple inputs
                        task_context["upstream_task_ids"] = task_def.upstream

                        # Copy upstream task results into context
                        for upstream_id in task_def.upstream:
                            if upstream_id in task_results:
                                task_context[upstream_id] = task_results[upstream_id]

                    # Notify observer and time execution
                    input_track_count = _get_input_track_count(
                        task_def, task_results
                    )
                    await node_observer.on_node_starting(
                        task_def, execution_order, total_nodes, input_track_count
                    )
                    start_ns = time.perf_counter_ns()

                    try:
                        result = await execute_node(node_type, task_context, config)
                    except Exception as exc:
                        duration_ms = (time.perf_counter_ns() - start_ns) // 1_000_000
                        await node_observer.on_node_failed(
                            task_def, exc, execution_order, total_nodes, duration_ms
                        )
                        raise

                    duration_ms = (time.perf_counter_ns() - start_ns) // 1_000_000
                    output_track_count = len(result["tracklist"].tracks)
                    await node_observer.on_node_completed(
                        task_def,
                        result,
                        execution_order,
                        total_nodes,
                        duration_ms,
                        input_track_count,
                        output_track_count,
                    )

                    # Store result in context and task_results
                    context[task_id] = result
                    task_results[task_id] = result

                    # Also store in context under node-specified result key if present
                    if task_def.result_key:
                        flow_logger.debug(
                            f"Storing result under key: {task_def.result_key}"
                        )
                        context[task_def.result_key] = result

                flow_logger.info("Workflow completed successfully")

                context["_task_results"] = task_results
                return context
            finally:
                # Close cached connector instances (httpx pools) on success or failure,
                # before the get_session() context manager exits
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
    popularity scores, etc.) into a comprehensive result structure.

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

    logger = get_run_logger()
    workflow_name = workflow_def.name

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
        logger.info(
            f"Starting workflow execution: {workflow_name} ({total_tasks} tasks)"
        )

    # Auto-create ProgressNodeObserver when progress_manager is active and no
    # explicit observer was provided
    effective_observer: NodeExecutionObserver | None = (
        observer  # type: ignore[assignment]  # Pydantic requires object; callers pass NodeExecutionObserver
    )
    if effective_observer is None and progress_manager and workflow_operation_id:
        effective_observer = ProgressNodeObserver(
            progress_manager, workflow_operation_id
        )

    try:
        with tags("workflow", workflow_name):
            # Start timing
            start_time = datetime.datetime.now(datetime.UTC)

            # Build and execute the workflow
            workflow = build_flow(workflow_def, observer=effective_observer)
            context = await workflow(
                workflow_progress_manager=progress_manager,
                workflow_operation_id=workflow_operation_id,
                **parameters,
            )

            # Calculate execution time
            end_time = datetime.datetime.now(datetime.UTC)
            execution_time = (end_time - start_time).total_seconds()

            # Extract typed task results from context
            task_results: dict[str, NodeResult] = context.pop("_task_results", {})

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

        logger.exception("Workflow execution failed")
        raise
