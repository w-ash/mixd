"""Executes music playlist workflows on a stdlib-asyncio DAG executor.

Converts declarative workflow definitions (JSON configs) into a concurrent
level-by-level execution: the task DAG is split into parallel levels
(``compute_parallel_levels``) and each level's independent nodes run together in
an ``asyncio.TaskGroup``. Handles playlist transformations like fetching tracks,
applying filters, enriching metadata, and creating/updating playlists across
music platforms, with progress tracking, fault tolerance, and per-run database
session management for long-running playlist operations.

The engine is deliberately Prefect-free: parallelism, run-state, retries
(``tenacity`` in connectors), cancellation (SIGTERM ``_shutdown_requested``), and
results are all owned here and in ``workflow_runs``.
"""

import asyncio
from collections.abc import Callable, Coroutine, Mapping
import datetime
import signal
import time
from typing import cast
from uuid import UUID

import attrs

from src.application.services.progress_broker import ProgressBroker
from src.application.workflows.definition.validation import (
    ConnectorNotAvailableError,
    extract_required_connectors,
    validate_connector_availability,
    validate_workflow_def,
)
from src.application.workflows.nodes.registry import get_node
from src.application.workflows.protocols import NodeExecutionObserver, NodeResult
from src.config.constants import BusinessLimits, NodeType, WorkflowConstants
from src.config.logging import get_logger, logging_context
from src.domain.entities.operations import OperationResult
from src.domain.entities.progress import (
    OperationStatus,
    create_progress_operation,
)
from src.domain.entities.shared import JsonValue, MetricValue
from src.domain.entities.workflow import (
    NodeExecutionEvent,
    NodeExecutionRecord,
    WorkflowDef,
    WorkflowTaskDef,
)

from .observers import NullNodeObserver, ProgressNodeObserver

logger = get_logger(__name__)

# One-time registry validation guard — runs before first workflow execution
_registry_validated = False


# --- Fault tolerance ---


# Categories where a node failure degrades rather than kills the workflow.
# Enricher failures are recoverable: downstream nodes use cached/stale metrics.
_RECOVERABLE_CATEGORIES: frozenset[str] = frozenset({"enricher"})


async def _safe_emit(
    awaitable: Coroutine[object, object, None], *, event: str, node_id: str
) -> None:
    """Run a lifecycle-observer call best-effort.

    Observers (SSE queue, progress bar, run-history writer) are decoupled
    telemetry — a failure in one must never fail the node or abort the run, and
    must never be misread as a node-execution failure. Exceptions are logged and
    swallowed; ``CancelledError`` still propagates so SIGTERM drain cancels the
    run as intended.
    """
    try:
        await awaitable
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning(
            "Observer emission failed",
            observer_event=event,
            node_id=node_id,
            exc_info=True,
        )


def _is_failure_recoverable(node_type: str) -> bool:
    """Check if a node failure should degrade rather than kill the workflow.

    Enricher failures are recoverable because the upstream tracklist can pass
    through unchanged and cached metrics from previous runs may still be
    available — the run completes visibly *degraded* rather than crashing.

    Degrading is not the same as data-safe: if a downstream metric filter then
    drops every (unenriched) track, the run nets 0 tracks. The destination
    node's empty-overwrite guard (``EmptyOverwriteError``) is what protects the
    user's playlist in that case — degrading keeps the run alive and visibly degraded, the
    guard keeps the data intact.

    Source/transform/destination failures remain fatal.
    """
    category = node_type.split(".", maxsplit=1)[0]
    return category in _RECOVERABLE_CATEGORIES


def _primary_upstream_id(task_def: WorkflowTaskDef) -> str | None:
    """Resolve the upstream whose tracklist a node treats as its primary input.

    Honors the node's configured ``primary_input`` when it names a real
    upstream; otherwise falls back to the first declared upstream. Shared by the
    success path and the enricher-degrade pass-through so both agree on which
    branch is "primary" (they had drifted: success honored ``primary_input``
    while degrade always took ``upstream[0]``).
    """
    if not task_def.upstream:
        return None
    primary = task_def.config.get("primary_input")
    if isinstance(primary, str) and primary in task_def.upstream:
        return primary
    return task_def.upstream[0]


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


def install_shutdown_handler() -> bool:
    """Install the process-wide SIGTERM handler once.

    Call once at process start (API lifespan, CLI entrypoint). Returns False on
    platforms/threads where signal handlers are unavailable (Windows, non-main
    thread). Replaces the former per-run register/reset: the per-run reset of the
    module-global flag let a starting run clobber an in-flight run's shutdown
    signal, and the per-run remove tore the handler down for still-running
    siblings.
    """
    loop = asyncio.get_running_loop()
    try:
        loop.add_signal_handler(signal.SIGTERM, _request_shutdown)
    except NotImplementedError, OSError:
        return False
    else:
        return True


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
    category = node_type.split(".", maxsplit=1)[0]
    if category in _CATEGORY_TIMEOUTS:
        return _CATEGORY_TIMEOUTS[category]
    return WorkflowConstants.TRANSFORM_TIMEOUT_SECONDS


# --- Node execution ---


async def execute_node(
    node_type: str, context: dict[str, object], config: Mapping[str, JsonValue]
) -> NodeResult:
    """Execute a single workflow node.

    Failure logging and progress tracking are handled by the observer in
    ``build_flow``'s execution loop — this function is intentionally thin.

    No retries here: source/enricher nodes retry via infrastructure tenacity
    policies; transform nodes are pure and deterministic (retrying won't help).
    """
    node_func, _ = get_node(node_type)
    enhanced_context = context.copy()
    enhanced_context.update({"node_type": node_type})
    return await node_func(enhanced_context, config)


# --- Flow building ---


def _get_input_track_count(
    task_def: WorkflowTaskDef, task_results: dict[str, NodeResult]
) -> int | None:
    """Extract track count from the primary upstream's result, if available."""
    upstream_id = _primary_upstream_id(task_def)
    if upstream_id is None:
        return None
    upstream_result = task_results.get(upstream_id)
    if upstream_result:
        return len(upstream_result["tracklist"].tracks)
    return None


# Type of the executable workflow coroutine returned by build_flow.
type _WorkflowFn = Callable[..., Coroutine[object, object, dict[str, object]]]


def build_flow(
    workflow_def: WorkflowDef,
    observer: NodeExecutionObserver | None = None,
    dry_run: bool = False,
    user_id: str = BusinessLimits.DEFAULT_USER_ID,
) -> _WorkflowFn:
    """Build an executable async workflow function from a typed definition.

    Computes parallel execution levels from the task DAG, then executes each
    level concurrently via ``asyncio.TaskGroup``. Tasks within a level are
    independent by definition — their dependencies are all in prior levels.

    A single event loop with level-based concurrency is used deliberately:
    spreading tasks across threads/loops (e.g. a thread-per-task runner) would
    break the ``asyncio.Queue``-based SSE observers. CPU-bound transform/combiner
    nodes are offloaded to worker threads inside ``node_factories`` so node work
    never blocks the loop.

    Args:
        workflow_def: Typed workflow definition with tasks, dependencies, and config.
        observer: Optional lifecycle observer for node start/complete/fail events.
        dry_run: When True, destination nodes skip external writes.
        user_id: Owner of the run; each task opens its own session under MVCC.

    Returns:
        Async function that executes the workflow and returns a context dict with
        ``_task_results`` and ``_node_records``.
    """
    from src.domain.entities.workflow import compute_parallel_levels

    node_observer = observer or NullNodeObserver()

    # Extract workflow metadata
    flow_name = workflow_def.name

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

    async def workflow_flow(
        workflow_progress_broker: ProgressBroker | None = None,
        workflow_operation_id: str | None = None,
        **parameters: object,
    ) -> dict[str, object]:
        """Executes workflow tasks level-by-level with concurrent independent nodes."""
        logger.info("Starting workflow")

        parameters["workflow_name"] = flow_name

        from src.application.workflows.context import create_workflow_context

        # Each task creates its own session from the PostgreSQL pool — no
        # shared session needed under MVCC.
        workflow_context = create_workflow_context(user_id=user_id)

        task_results: dict[str, NodeResult] = {}
        node_records: list[NodeExecutionRecord] = []

        async def _run_node_lifecycle(
            task_def: WorkflowTaskDef,
        ) -> tuple[str, NodeResult | Exception]:
            """Execute one node with full lifecycle management.

            Wraps observer notification, timeout, error handling, and
            diagnostics. **Total by contract:** it never raises a plain
            ``Exception`` — it returns ``(task_id, NodeResult)`` on success or
            degrade, and ``(task_id, Exception)`` when the failure is fatal. Only
            ``CancelledError`` (a ``BaseException``) is allowed to propagate. This
            keeps the level's ``TaskGroup`` from ever cancelling siblings or
            raising an ``ExceptionGroup``: the group can only complete normally or
            (on external cancellation) re-raise a bare ``CancelledError``, which
            the run's ``except CancelledError`` maps to ``crashed`` one layer up.

            The caller discriminates the outcome via ``isinstance(_, Exception)``.
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
                return (task_id, WorkflowCancelledError("Shutdown requested"))

            async def _run_task_inner() -> tuple[str, NodeResult | Exception]:
                """Run the node's observer/timeout/diagnostics lifecycle.

                Extracted from ``_run_node_lifecycle`` so the protective ``try``
                clause stays small; the same statements remain guarded by the
                caller's ``CancelledError``/``Exception`` totality net. Returns
                ``(task_id, NodeResult)`` on success/degrade or ``(task_id,
                Exception)`` on a fatal node failure.
                """
                logger.info(f"Starting task: {task_id} (type: {node_type})")

                # Build task-specific context from static metadata + upstream
                # results. Avoids copying the whole context bag (which grows with
                # each completed node) — only what this task needs.
                task_context: dict[str, object] = {
                    "parameters": parameters,
                    "workflow_context": workflow_context,
                    "workflow_name": flow_name,
                    "progress_broker": workflow_progress_broker,
                    "workflow_operation_id": workflow_operation_id,
                    "total_tasks": total_nodes,
                    "dry_run": dry_run,
                    "current_step": execution_order,
                }

                if task_def.upstream:
                    task_context["upstream_task_id"] = _primary_upstream_id(task_def)
                    task_context["upstream_task_ids"] = task_def.upstream

                    for upstream_id in task_def.upstream:
                        if upstream_id in task_results:
                            task_context[upstream_id] = task_results[upstream_id]

                input_track_count = _get_input_track_count(task_def, task_results)
                base_event = NodeExecutionEvent(
                    task_def=task_def,
                    execution_order=execution_order,
                    total_nodes=total_nodes,
                    input_track_count=input_track_count,
                )
                timeout_seconds = _get_node_timeout(node_type)
                start_ns = time.perf_counter_ns()

                was_degraded = False
                try:
                    await _safe_emit(
                        node_observer.on_node_starting(base_event),
                        event="on_node_starting",
                        node_id=task_id,
                    )
                    async with asyncio.timeout(timeout_seconds):
                        result = await execute_node(node_type, task_context, config)
                except Exception as exc:
                    duration_ms = (time.perf_counter_ns() - start_ns) // 1_000_000

                    if isinstance(exc, TimeoutError):
                        exc = TimeoutError(
                            f"Node '{task_id}' ({node_type}) exceeded "
                            f"{timeout_seconds}s timeout"
                        )

                    logger.error(
                        "Node execution failed",
                        node_id=task_id,
                        node_type=node_type,
                        execution_order=execution_order,
                        total_nodes=total_nodes,
                        duration_ms=duration_ms,
                        exc_info=True,
                    )
                    failed_event = attrs.evolve(base_event, duration_ms=duration_ms)
                    await _safe_emit(
                        node_observer.on_node_failed(failed_event, exc),
                        event="on_node_failed",
                        node_id=task_id,
                    )

                    # Fault tolerance: enricher failures degrade rather than kill
                    primary_upstream_id = _primary_upstream_id(task_def)
                    if (
                        _is_failure_recoverable(node_type)
                        and primary_upstream_id is not None
                        and primary_upstream_id in task_results
                    ):
                        upstream_result = task_results[primary_upstream_id]
                        result = upstream_result  # pass through primary tracklist
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
                            failed_event.to_record(
                                status="failed", error_message=str(exc)
                            )
                        )
                        return (task_id, exc)
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
                    dropped_ids: list[UUID | None] = []
                    primary_id = _primary_upstream_id(task_def)
                    if input_track_count and primary_id is not None:
                        upstream_res = task_results.get(primary_id)
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
                    await _safe_emit(
                        node_observer.on_node_completed(completed_event, result),
                        event="on_node_completed",
                        node_id=task_id,
                    )
                    node_records.append(completed_event.to_record(status="completed"))

                # Store result key alias so downstream nodes can reference it
                if task_def.result_key:
                    logger.debug(f"Storing result under key: {task_def.result_key}")
                    task_results[task_def.result_key] = result

                return (task_id, result)

            try:
                return await _run_task_inner()
            except asyncio.CancelledError:
                # Cooperative/forced cancellation (SIGTERM). Must propagate as a
                # bare CancelledError so the run is recorded `crashed`, not `failed`.
                raise
            except Exception as exc:
                # Totality safety net: an unexpected failure outside node
                # execution (e.g. an observer callback raising). Today this would
                # propagate and fail the workflow — preserve that by returning it
                # fatal, without poisoning the level's TaskGroup.
                logger.error(
                    "Node lifecycle error",
                    node_id=task_id,
                    node_type=node_type,
                    exc_info=True,
                )
                node_records.append(
                    NodeExecutionRecord(
                        node_id=task_id,
                        node_type=node_type,
                        execution_order=execution_order,
                        status="failed",
                        error_message=str(exc),
                    )
                )
                return (task_id, exc)

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

                # Run the level's independent nodes concurrently. Children are
                # total (never raise except CancelledError), so the group only
                # completes normally or re-raises a bare CancelledError.
                async with asyncio.TaskGroup() as tg:
                    tasks = [tg.create_task(_run_node_lifecycle(td)) for td in level]

                # Read outcomes in submission order (== prior asyncio.gather order)
                # so the first-submitted fatal wins. .result() never re-raises
                # here because the children are total. Raising the fatal *after*
                # the group has closed keeps it a bare exception (not wrapped in an
                # ExceptionGroup), preserving the crashed/failed mapping upstream.
                fatal_error: Exception | None = None
                for task in tasks:
                    task_id, outcome = task.result()
                    if isinstance(outcome, Exception):
                        if fatal_error is None:
                            fatal_error = outcome
                    else:
                        task_results[task_id] = outcome
                        completed_count += 1
                if fatal_error is not None:
                    raise fatal_error

            logger.info("Workflow completed successfully")

            return {
                "_task_results": task_results,
                "_node_records": node_records,
            }
        finally:
            # Close cached connector instances (httpx pools) on success or failure.
            # Shielded so a CancelledError arriving during SIGTERM (deploy/autoscale)
            # can't abort the close mid-flight and leak pools into the next process.
            # aclose() is idempotent, so the shield is safe. A strong reference is
            # held because the loop keeps only a weak ref to the shielded task — see
            # asyncio.shield docs.
            cleanup = asyncio.ensure_future(workflow_context.connectors.aclose())
            try:
                await asyncio.shield(cleanup)
            except Exception:
                # A connector-close failure must not mask the real run outcome
                # (a node failure or WorkflowCancelledError being propagated out
                # of this finally). CancelledError still propagates as intended.
                logger.warning(
                    "Connector cleanup failed during teardown", exc_info=True
                )

    return workflow_flow


# --- Workflow execution ---


def _aggregate_workflow_metrics(
    task_results: dict[str, NodeResult],
) -> dict[str, dict[UUID, MetricValue]]:
    """Aggregate metrics from all workflow task results.

    Iterates through task results, extracting metrics from each tracklist's
    metadata and merging them into a unified dict.
    """
    all_metrics: dict[str, dict[UUID, MetricValue]] = {}

    for task_id, result in task_results.items():
        tracklist = result["tracklist"]
        task_metrics = tracklist.metadata.get("metrics", {})

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


async def run_workflow(
    workflow_def: WorkflowDef,
    progress_broker: ProgressBroker | None = None,
    observer: NodeExecutionObserver | None = None,
    dry_run: bool = False,
    user_id: str = BusinessLimits.DEFAULT_USER_ID,
    **parameters: object,
) -> OperationResult:
    """Executes complete playlist workflow from JSON definition to final result.

    Main entry point for workflow execution. Builds the executable workflow from
    the definition, executes all tasks with proper dependency ordering, times
    execution, and extracts final results with aggregated metrics.

    Args:
        workflow_def: Typed workflow definition with tasks and dependencies.
        progress_broker: Optional ProgressBroker for CLI progress tracking.
        observer: Optional NodeExecutionObserver for node lifecycle events. When
            progress_broker is provided and no explicit observer, a
            ProgressNodeObserver is created automatically.
        dry_run: When True, destination nodes skip external writes.
        user_id: Owner of the run.
        **parameters: Dynamic parameters passed to workflow tasks.

    Returns:
        Structured operation result with final tracks and aggregated metrics.
    """

    logger.info(
        "run_workflow entered",
        workflow_id=workflow_def.id,
        workflow_name=workflow_def.name,
    )

    global _registry_validated
    if not _registry_validated:
        from src.application.workflows.nodes.registry_validation import (
            validate_registry,
        )

        validate_registry()
        _registry_validated = True

    validate_workflow_def(workflow_def)

    # Pre-flight connector validation — fail fast before any I/O
    required_connectors = extract_required_connectors(workflow_def)
    if required_connectors:
        from src.application.workflows.context import ConnectorRegistryImpl

        available = ConnectorRegistryImpl().list_connectors()
        missing = validate_connector_availability(required_connectors, available)
        if missing:
            raise ConnectorNotAvailableError(missing)

    # Concurrency is guarded at the DB (uq_workflow_runs_active): the pending
    # run row is inserted before this executor is invoked, so a second
    # concurrent run is rejected at create_run, not here.
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
        with logging_context(
            workflow_id=workflow_def.id,
            workflow_name=workflow_name,
            workflow_run_id=workflow_run_id,
        ):
            # Initialize workflow-level progress tracking
            workflow_operation_id = None
            if progress_broker:
                total_tasks = len(workflow_def.tasks)

                workflow_operation = create_progress_operation(
                    description=f"Executing {workflow_name}", total_items=total_tasks
                )
                workflow_operation_id = await progress_broker.start_operation(
                    workflow_operation
                )
                logger.info(
                    f"Starting workflow execution: {workflow_name} ({total_tasks} tasks)"
                )

            # Compose observers: always add ProgressNodeObserver when progress_broker
            # is active, even if an explicit observer (e.g. RunHistoryObserver) is provided.
            # This enables CLI to get both Rich progress bars AND DB run history.
            from .observers import CompositeNodeObserver

            typed_observers: list[NodeExecutionObserver] = []
            if observer is not None:
                typed_observers.append(observer)
            if progress_broker and workflow_operation_id:
                typed_observers.append(
                    ProgressNodeObserver(progress_broker, workflow_operation_id)
                )

            effective_observer: NodeExecutionObserver | None
            if len(typed_observers) > 1:
                effective_observer = CompositeNodeObserver(typed_observers)
            elif typed_observers:
                effective_observer = typed_observers[0]
            else:
                effective_observer = None

            async def _build_and_execute_workflow() -> OperationResult:
                """Build the flow, run it, and assemble the workflow result.

                Extracted from ``run_workflow`` so the protective ``try`` clause
                stays small; the same statements remain guarded by the caller's
                broad ``except Exception`` that marks the workflow failed.
                """
                # Start timing
                start_time = datetime.datetime.now(datetime.UTC)

                # Build and execute the workflow
                workflow_fn = build_flow(
                    workflow_def,
                    observer=effective_observer,
                    dry_run=dry_run,
                    user_id=user_id,
                )
                context = await workflow_fn(
                    workflow_progress_broker=progress_broker,
                    workflow_operation_id=workflow_operation_id,
                    **parameters,
                )

                # Calculate execution time
                end_time = datetime.datetime.now(datetime.UTC)
                execution_time = (end_time - start_time).total_seconds()

                # Extract typed task results from context
                task_results: dict[str, NodeResult] = cast(
                    "dict[str, NodeResult]",
                    context.pop("_task_results", {}),
                )

                # Extract result with actual execution time
                result = extract_workflow_result(
                    workflow_def,
                    task_results,
                    execution_time,
                )

                # Complete workflow-level progress tracking
                if progress_broker and workflow_operation_id:
                    await progress_broker.complete_operation(
                        workflow_operation_id, OperationStatus.COMPLETED
                    )

                return result

            try:
                return await _build_and_execute_workflow()
            except Exception:
                # Mark workflow progress as failed
                if progress_broker and workflow_operation_id:
                    await progress_broker.complete_operation(
                        workflow_operation_id, OperationStatus.FAILED
                    )

                logger.error("Workflow failed — see node error above")
                raise
    finally:
        remove_workflow_run_logger(run_sink_id)
