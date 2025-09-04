"""Executes music playlist workflows using the Prefect v3 orchestration engine.

Converts declarative workflow definitions (JSON configs) into executable Prefect flows.
Handles playlist transformations like fetching tracks, applying filters, enriching metadata,
and creating/updating playlists across music platforms. Provides progress tracking, error
recovery, and database session management for long-running playlist operations.
"""

from collections.abc import Callable
import datetime
from typing import TYPE_CHECKING, Any, NotRequired, TypedDict

if TYPE_CHECKING:
    from uuid import UUID

from prefect import flow, tags, task
from prefect.artifacts import create_progress_artifact, update_progress_artifact
from prefect.cache_policies import NONE
from prefect.logging import get_run_logger

# Use Narada's standard logger for module-level logging; Prefect tasks use get_run_logger()
from src.config.logging import get_logger
from src.domain.entities.operations import WorkflowResult

from .node_registry import get_node

logger = get_logger(__name__)


# --- Simple progress feedback for CLI ---

_simple_callback: Callable | None = None


def register_simple_progress_callback(callback: Callable) -> None:
    """Registers callback function for CLI progress updates during workflow execution."""
    global _simple_callback
    _simple_callback = callback


def _emit_simple_event(event_type: str, event_data: dict[str, Any]) -> None:
    """Sends workflow progress events to registered callback function if available."""
    if _simple_callback:
        try:
            _simple_callback(event_type, event_data)
        except Exception as e:
            logger.warning(f"Simple progress callback error: {e}")


# --- Node execution ---


def _should_show_progress_for_node(node_type: str) -> bool:
    """Returns True for node types that process large datasets or make many API calls.

    Shows progress artifacts for:
    - source.playlist nodes (fetch large playlists)
    - enricher.* nodes (multiple API requests for metadata)
    """
    # Source nodes that fetch large playlists
    if node_type.startswith("source.playlist"):
        return True

    # Enrichment operations that make many API calls
    return node_type.startswith("enricher.")


class TaskResult(TypedDict):
    """Prefect task execution result structure."""

    success: bool
    result: Any
    error: NotRequired[str]


@task(
    retries=3,
    retry_delay_seconds=30,
    tags=["node"],
    cache_policy=NONE,  # Disable caching due to non-serializable context objects
)
async def execute_node(node_type: str, context: dict, config: dict) -> dict:
    """Executes a single workflow node (source, filter, enricher, etc.) as a Prefect task.

    Wraps node execution with Prefect's retry logic, logging, and progress tracking.
    Creates progress artifacts for long-running operations like playlist fetching
    and metadata enrichment.

    Args:
        node_type: Node identifier (e.g., "source.playlist", "enricher.spotify")
        context: Shared workflow context including database session and upstream results
        config: Node-specific configuration parameters

    Returns:
        Node execution result (typically containing processed tracklist)
    """
    # Use Prefect's run logger to get task context
    task_logger = get_run_logger()

    # Log node execution
    task_logger.info(f"Executing node: {node_type}")

    # Get node implementation
    node_func, _ = get_node(node_type)

    # Create progress artifact for potentially long-running operations
    progress_artifact_id: UUID | None = None
    if _should_show_progress_for_node(node_type):
        try:
            progress_artifact_id = await create_progress_artifact(  # type: ignore[misc]
                progress=0.0,
                description=f"Processing {node_type.replace('_', ' ').title()}",
            )
            task_logger.info(f"Created progress artifact for {node_type}")
        except Exception as e:
            task_logger.warning(f"Failed to create progress artifact: {e}")

    try:
        # Note: We only use progress artifacts for start/completion to avoid async/sync mismatch
        # Real-time progress updates in sync callbacks cannot properly await async Prefect functions
        # The progress system will still provide CLI feedback through other channels

        # Execute node with direct configuration
        # No template resolution - config passes through unchanged
        result = await node_func(context, config)

        # Mark progress as complete
        if progress_artifact_id:
            try:
                await update_progress_artifact(
                    artifact_id=progress_artifact_id, progress=1.0
                )  # type: ignore[misc]
            except Exception as e:
                task_logger.warning(
                    f"Failed to update progress artifact completion: {e}"
                )

        task_logger.info(f"Node completed successfully: {node_type}")
        return result

    except Exception:
        task_logger.exception(f"Node failed (type: {node_type})")
        raise


# --- Flow building ---


def generate_flow_run_name(flow_name: str) -> str:
    """Creates timestamped flow run name for Prefect UI identification."""
    return (
        f"{flow_name}-{datetime.datetime.now(datetime.UTC).strftime('%Y%m%d-%H%M%S')}"
    )


def build_flow(workflow_def: dict) -> Any:
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

    # Create a topological sort of tasks based on dependencies
    def topological_sort(tasks):
        """Orders tasks by dependencies to ensure upstream tasks execute first."""
        # Create a dependency graph
        graph = {task["id"]: task.get("upstream", []) for task in tasks}

        # Find execution order
        visited = set()
        result = []

        def visit(node_id):
            if node_id in visited:
                return
            visited.add(node_id)
            for dep in graph[node_id]:
                visit(dep)
            result.append(next(t for t in tasks if t["id"] == node_id))

        for task_id in graph:
            visit(task_id)

        return result

    # Sort tasks in execution order
    sorted_tasks = topological_sort(tasks)

    @flow(
        name=flow_name,
        description=flow_description,
        flow_run_name=generate_flow_run_name(flow_name),
    )
    async def workflow_flow(**parameters):
        """Executes workflow tasks in dependency order with shared database session."""
        # Use Prefect's run logger to get flow context
        flow_logger = get_run_logger()
        flow_logger.info("Starting workflow")

        # Emit simple workflow started event for CLI feedback
        _emit_simple_event("workflow_started", {"workflow_name": flow_name})

        # Create workflow context with all required providers
        from src.infrastructure.persistence.database.db_connection import get_session

        from .context import SharedSessionProvider, create_workflow_context

        # Create a single shared session for the entire workflow execution
        async with get_session() as shared_session:
            # Create shared session provider that wraps the session
            shared_session_provider = SharedSessionProvider(shared_session)

            # Create workflow context with shared session
            workflow_context = create_workflow_context(shared_session)

            # Initialize execution context with shared session provider
            context = {
                "parameters": parameters,
                "use_cases": workflow_context.use_cases,  # Database operations and business logic
                "connectors": workflow_context.connectors,
                "config": workflow_context.config,
                "logger": workflow_context.logger,
                "session_provider": shared_session_provider,  # Use shared session
                "shared_session": shared_session,  # Direct access for nodes that need it
                "workflow_context": workflow_context,  # Full context for UoW execution
            }
            task_results = {}

            # Execute tasks in dependency order
            for task_def in sorted_tasks:
                task_id = task_def["id"]
                node_type = task_def["type"]

                # Log the task start
                flow_logger.info(f"Starting task: {task_id} (type: {node_type})")

                # Emit simple task started event for CLI feedback
                _emit_simple_event(
                    "task_started",
                    {"task_id": task_id, "task_name": task_id, "task_type": node_type},
                )

                # Resolve configuration with current context
                config = task_def.get("config", {})

                # Create task-specific context with upstream results
                task_context = context.copy()

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
                            task_context["upstream_task_id"] = task_def["upstream"][0]

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

                # Emit simple task completed event for CLI feedback
                _emit_simple_event(
                    "task_completed",
                    {
                        "task_id": task_id,
                        "task_name": task_id,
                        "task_type": node_type,
                        "result": result,
                    },
                )

            flow_logger.info("Workflow completed successfully")

            # Emit simple workflow completed event for CLI feedback
            _emit_simple_event("workflow_completed", {"workflow_name": flow_name})

            return context

    # Return the decorated flow function
    return workflow_flow


# --- Workflow execution ---


@task(
    name="extract_workflow_result",
    description="Extract workflow result with metrics",
    cache_policy=NONE,  # Disable caching due to non-serializable context objects
)
async def extract_workflow_result(  # noqa: RUF029
    workflow_def: dict,
    task_results: dict,
    flow_run_name: str,
    execution_time: float,
) -> WorkflowResult:
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

    # Extract all metrics from task results
    all_metrics = {}

    for task_id, result in task_results.items():
        if isinstance(result, dict) and "tracklist" in result:
            task_metrics = result["tracklist"].metadata.get("metrics", {})

            # Log metrics information for debugging
            for metric_name, values in task_metrics.items():
                if values:
                    metric_keys = list(values.keys())
                    logger.debug(
                        f"Metrics found in {task_id}",
                        metric_name=metric_name,
                        key_count=len(metric_keys),
                        key_type=str(type(metric_keys[0])) if metric_keys else "N/A",
                        sample_values_count=sum(1 for v in values.values() if v != 0),
                    )

            # Add to all_metrics - make deep copy to ensure values are preserved
            for metric_name, values in task_metrics.items():
                if metric_name not in all_metrics:
                    all_metrics[metric_name] = {}
                # Ensure we're not losing any values during update
                all_metrics[metric_name].update(values.copy())

    # Verify final metrics
    if "spotify_popularity" in all_metrics:
        sp_keys = list(all_metrics["spotify_popularity"].keys())
        logger.debug(
            "Final spotify_popularity metrics",
            key_count=len(sp_keys),
            key_type=str(type(sp_keys[0])) if sp_keys else "N/A",
            sample_keys=sp_keys[:5],
            sample_values=[
                all_metrics["spotify_popularity"].get(k) for k in sp_keys[:5]
            ]
            if sp_keys
            else [],
        )

    logger.debug(
        "Final extracted metrics",
        metric_names=list(all_metrics.keys()),
        spotify_popularity_count=len(all_metrics.get("spotify_popularity", {})),
    )

    return WorkflowResult(
        tracks=final_tracks,
        metrics=all_metrics,
        operation_name=workflow_def.get("name", flow_run_name),
        execution_time=execution_time,
        tracklist=final_tracklist,
    )


@flow(name="run_workflow")
async def run_workflow(workflow_def: dict, **parameters) -> tuple[dict, WorkflowResult]:
    """Executes complete playlist workflow from JSON definition to final result.

    Main entry point for workflow execution. Builds Prefect flow from definition,
    executes all tasks with proper dependency ordering, times execution, and
    extracts final results with aggregated metrics.

    Args:
        workflow_def: JSON workflow definition with tasks and dependencies
        **parameters: Dynamic parameters passed to workflow tasks

    Returns:
        Tuple of (execution context with all task results, structured final result)
    """

    logger = get_run_logger()
    workflow_name = workflow_def.get("name", "unnamed")

    try:
        with tags("workflow", workflow_name):
            logger.info(f"Running workflow: {workflow_name}")

            # Start timing
            start_time = datetime.datetime.now(datetime.UTC)

            # Build and execute the workflow
            workflow = build_flow(workflow_def)
            context = await workflow(**parameters)

            # Calculate execution time
            end_time = datetime.datetime.now(datetime.UTC)
            execution_time = (end_time - start_time).total_seconds()

            # Add metadata to context
            context["workflow_name"] = workflow_name

            # Submit task and get result with actual execution time
            flow_run_name = workflow.flow_run_name
            result = await extract_workflow_result(
                workflow_def,
                context,
                flow_run_name,
                execution_time,
            )

            return context, result
    except Exception:
        logger.exception("Workflow execution failed")
        raise
