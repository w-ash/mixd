"""Executes music playlist workflows using the Prefect v3 orchestration engine.

Converts declarative workflow definitions (JSON configs) into executable Prefect flows.
Handles playlist transformations like fetching tracks, applying filters, enriching metadata,
and creating/updating playlists across music platforms. Provides progress tracking, error
recovery, and database session management for long-running playlist operations.
"""

# pyright: reportExplicitAny=false, reportAny=false

import datetime
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
    ProgressStatus,
    create_progress_event,
    create_progress_operation,
)

from .node_registry import get_node
from .protocols import NodeResult

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

        # Update unified workflow progress after node completion
        progress_manager = context.get("progress_manager")
        workflow_operation_id = context.get("workflow_operation_id")
        if progress_manager and workflow_operation_id:
            current_step = context.get("current_step", 0)
            total_tasks = context.get("total_tasks", 1)

            # Create friendly display name for the node type
            display_name = node_type.replace("_", " ").replace(".", " ").title()

            # Emit progress event for completed node
            event = create_progress_event(
                operation_id=workflow_operation_id,
                current=current_step,
                total=total_tasks,
                message=f"Completed {display_name}",
                status=ProgressStatus.IN_PROGRESS,
            )
            await progress_manager.emit_progress(event)

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


def topological_sort(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Orders tasks by dependencies to ensure upstream tasks execute first.

    Uses 3-color DFS to detect cycles: unvisited → visiting (gray) → visited (black).
    A back-edge to a gray node means a cycle exists.
    """
    graph = {task["id"]: task.get("upstream", []) for task in tasks}

    visited: set[str] = set()
    visiting: set[str] = set()
    result: list[dict[str, Any]] = []

    task_by_id = {task["id"]: task for task in tasks}

    def visit(node_id: str) -> None:
        if node_id in visited:
            return
        if node_id in visiting:
            raise ValueError(f"Cycle detected in workflow: {node_id}")
        visiting.add(node_id)
        for dep in graph.get(node_id, []):
            visit(dep)
        visiting.discard(node_id)
        visited.add(node_id)
        if node_id in task_by_id:
            result.append(task_by_id[node_id])

    for task_id in graph:
        visit(task_id)

    return result


# Required config keys per node type — derived from node function contracts.
# Nodes not listed here have no required config keys (all optional with defaults).
_REQUIRED_CONFIG: dict[str, list[str]] = {
    "source.playlist": ["playlist_id"],
    "filter.by_metric": ["metric_name"],
    "filter.by_tracks": ["exclusion_source"],
    "filter.by_artists": ["exclusion_source"],
    "filter.by_liked_status": ["service"],
    "selector.percentage": ["percentage"],
    "destination.create_playlist": ["name"],
    "destination.update_playlist": ["playlist_id"],
}

# Expected types for required config values — catches type mismatches
# (e.g., playlist_id: 123 instead of "abc-123") before expensive I/O runs.
# Values are single types or tuples of types (for isinstance() checks).
_REQUIRED_CONFIG_TYPES: dict[str, dict[str, type | tuple[type, ...]]] = {
    "source.playlist": {"playlist_id": str},
    "filter.by_metric": {"metric_name": str},
    "filter.by_tracks": {"exclusion_source": str},
    "filter.by_artists": {"exclusion_source": str},
    "filter.by_liked_status": {"service": str},
    "selector.percentage": {"percentage": (int, float)},
    "destination.create_playlist": {"name": str},
    "destination.update_playlist": {"playlist_id": str},
}


def _validate_node_config(node_type: str, config: dict[str, Any], task_id: str) -> None:
    """Validate that a node's config contains all required keys with correct types.

    Called during workflow definition validation to catch config errors
    before any expensive I/O operations run. Nodes not in _REQUIRED_CONFIG
    have no mandatory keys and pass validation unconditionally.

    Raises:
        ValueError: If required config keys are missing, have wrong types,
            or are empty strings when a non-empty string is required.
    """
    required = _REQUIRED_CONFIG.get(node_type, [])
    missing = [k for k in required if k not in config]
    if missing:
        raise ValueError(
            f"Task '{task_id}' (type '{node_type}') missing required config: {missing}"
        )

    # Validate value types for required keys
    type_requirements = _REQUIRED_CONFIG_TYPES.get(node_type, {})
    for key, expected_type in type_requirements.items():
        if key not in config:
            continue
        value = config[key]
        if not isinstance(value, expected_type):
            type_name = (
                expected_type.__name__
                if isinstance(expected_type, type)
                else " | ".join(t.__name__ for t in expected_type)
            )
            raise ValueError(  # noqa: TRY004  # consistent with other config validation errors
                f"Task '{task_id}' config key '{key}' must be {type_name}, "
                f"got {type(value).__name__}: {value!r}"
            )
        # Reject empty strings for required string keys
        if isinstance(value, str) and not value.strip():
            raise ValueError(f"Task '{task_id}' config key '{key}' must not be empty")


def validate_workflow_def(workflow_def: dict[str, Any]) -> None:
    """Validate workflow definition structure before execution.

    Catches structural errors early — before any expensive I/O operations run.
    Checks for: non-empty tasks, required fields, valid upstream references,
    and resolvable node types.

    Raises:
        ValueError: If the workflow definition is structurally invalid.
    """
    tasks = workflow_def.get("tasks", [])
    if not tasks:
        raise ValueError("Workflow has no tasks")

    task_ids: set[str] = set()
    for task_def in tasks:
        if "id" not in task_def:
            raise ValueError(f"Task missing required 'id' field: {task_def}")
        if "type" not in task_def:
            raise ValueError(f"Task '{task_def['id']}' missing required 'type' field")
        task_ids.add(task_def["id"])

    # Validate upstream references point to existing tasks
    for task_def in tasks:
        for upstream_id in task_def.get("upstream", []):
            if upstream_id not in task_ids:
                raise ValueError(
                    f"Task '{task_def['id']}' references unknown upstream '{upstream_id}'. Available: {sorted(task_ids)}"
                )

    # Validate node types are resolvable in the registry
    for task_def in tasks:
        node_type = task_def["type"]
        try:
            get_node(node_type)
        except KeyError:
            raise ValueError(
                f"Task '{task_def['id']}' has unknown node type '{node_type}'"
            ) from None

    # Validate node config required keys per node type
    for task_def in tasks:
        config = task_def.get("config", {})
        _validate_node_config(task_def["type"], config, task_id=task_def["id"])


def build_flow(workflow_def: dict[str, Any]) -> Any:
    """Converts workflow definition JSON into executable Prefect flow function.

    Performs topological sort of tasks based on dependencies, creates shared database
    session, and builds dynamic flow that executes nodes in correct order while
    passing results between dependent tasks.

    Args:
        workflow_def: JSON workflow definition with tasks, dependencies, and config

    Returns:
        Async Prefect flow function ready for execution
    """

    # Extract workflow metadata
    flow_name = workflow_def.get("name", "unnamed_workflow")
    flow_description = workflow_def.get("description", "")
    tasks = workflow_def.get("tasks", [])

    # Sort tasks in execution order
    sorted_tasks = topological_sort(tasks)

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

            # Initialize execution context
            context = {
                "parameters": parameters,
                "workflow_context": workflow_context,
                "workflow_name": flow_name,
                "progress_manager": workflow_progress_manager,
                "workflow_operation_id": workflow_operation_id,
                "total_tasks": len(sorted_tasks),
            }
            task_results: dict[str, NodeResult] = {}

            try:
                # Execute tasks in dependency order
                for task_index, task_def in enumerate(sorted_tasks):
                    task_id = task_def["id"]
                    node_type = task_def["type"]

                    # Log the task start
                    flow_logger.info(f"Starting task: {task_id} (type: {node_type})")

                    # Resolve configuration with current context
                    config = task_def.get("config", {})

                    # Create task-specific context with upstream results and progress tracking
                    task_context = context.copy()
                    task_context["current_step"] = (
                        task_index + 1
                    )  # 1-based indexing for progress

                    if task_def.get("upstream"):
                        if len(task_def["upstream"]) == 1:
                            # Single upstream case
                            task_context["upstream_task_id"] = task_def["upstream"][0]
                        else:
                            # Multiple upstream case - first one is primary by convention
                            # (unless config specifies a primary_input)
                            primary_input = config.get("primary_input")
                            if primary_input and primary_input in task_def["upstream"]:
                                task_context["upstream_task_id"] = primary_input
                            else:
                                task_context["upstream_task_id"] = task_def["upstream"][
                                    0
                                ]

                        # Add all upstream tasks as a list for nodes that need multiple inputs
                        task_context["upstream_task_ids"] = task_def["upstream"]

                        # Copy upstream task results into context
                        for upstream_id in task_def["upstream"]:
                            if upstream_id in task_results:
                                task_context[upstream_id] = task_results[upstream_id]

                    # Execute node with Prefect's native progress tracking
                    result = await execute_node(node_type, task_context, config)

                    # Store result in context and task_results
                    context[task_id] = result
                    task_results[task_id] = result

                    # Also store in context under node-specified result key if present
                    if result_key := task_def.get("result_key"):
                        flow_logger.debug(f"Storing result under key: {result_key}")
                        context[result_key] = result

                    # Task completed - progress tracking handled in execute_node

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
    workflow_def: dict[str, Any],
    task_results: dict[str, NodeResult],
    flow_run_name: str,
    execution_time: float,
) -> OperationResult:
    """Extracts final tracklist and aggregates metrics from all workflow tasks.

    Finds the destination task (playlist creation/update) to get the final filtered
    tracklist, then combines metrics from all intermediate tasks (play counts,
    popularity scores, etc.) into a comprehensive result structure.

    Args:
        workflow_def: Original workflow definition
        task_results: Results from all executed tasks
        flow_run_name: Prefect flow run identifier
        execution_time: Total workflow execution time in seconds

    Returns:
        Structured result with final tracks, aggregated metrics, and timing info
    """

    # Find the destination task - it should be the last one in the workflow
    destination_task = next(
        (
            t
            for t in reversed(workflow_def.get("tasks", []))
            if t.get("type", "").startswith("destination.")
        ),
        None,
    )

    if not destination_task:
        raise ValueError("No destination task found in workflow")

    destination_id = destination_task["id"]

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
        operation_name=workflow_def.get("name", flow_run_name),
        execution_time=execution_time,
        tracklist=final_tracklist,
    )


@flow(name="run_workflow")
async def run_workflow(
    workflow_def: dict[str, Any],
    progress_manager: AsyncProgressManager | None = None,
    **parameters: object,
) -> tuple[dict[str, Any], OperationResult]:
    """Executes complete playlist workflow from JSON definition to final result.

    Main entry point for workflow execution. Builds Prefect flow from definition,
    executes all tasks with proper dependency ordering, times execution, and
    extracts final results with aggregated metrics.

    Args:
        workflow_def: JSON workflow definition with tasks and dependencies
        progress_manager: Optional AsyncProgressManager for CLI progress tracking
        **parameters: Dynamic parameters passed to workflow tasks

    Returns:
        Tuple of (execution context with all task results, structured final result)
    """

    global _registry_validated
    if not _registry_validated:
        from .registry_validation import validate_registry

        validate_registry()
        _registry_validated = True

    validate_workflow_def(workflow_def)

    logger = get_run_logger()
    workflow_name = workflow_def.get("name", "unnamed")

    # Initialize workflow-level progress tracking
    workflow_operation_id = None
    if progress_manager:
        tasks = workflow_def.get("tasks", [])
        total_tasks = len(tasks)

        workflow_operation = create_progress_operation(
            description=f"Executing {workflow_name}", total_items=total_tasks
        )
        workflow_operation_id = await progress_manager.start_operation(
            workflow_operation
        )
        logger.info(
            f"Starting workflow execution: {workflow_name} ({total_tasks} tasks)"
        )

    try:
        with tags("workflow", workflow_name):
            # Start timing
            start_time = datetime.datetime.now(datetime.UTC)

            # Build and execute the workflow
            workflow = build_flow(workflow_def)
            context = await workflow(
                workflow_progress_manager=progress_manager,
                workflow_operation_id=workflow_operation_id,
                **parameters,
            )

            # Calculate execution time
            end_time = datetime.datetime.now(datetime.UTC)
            execution_time = (end_time - start_time).total_seconds()

            # Add metadata to context
            context["workflow_name"] = workflow_name

            # Extract typed task results from context
            task_results: dict[str, NodeResult] = context.pop("_task_results", {})

            # Extract result with actual execution time
            flow_run_name = workflow.flow_run_name
            result = extract_workflow_result(
                workflow_def,
                task_results,
                flow_run_name,
                execution_time,
            )

            # Complete workflow-level progress tracking
            if progress_manager and workflow_operation_id:
                await progress_manager.complete_operation(
                    workflow_operation_id, OperationStatus.COMPLETED
                )

            return context, result
    except Exception:
        # Mark workflow progress as failed
        if progress_manager and workflow_operation_id:
            await progress_manager.complete_operation(
                workflow_operation_id, OperationStatus.FAILED
            )

        logger.exception("Workflow execution failed")
        raise
