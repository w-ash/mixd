"""Workflow definition validation and DAG utilities.

Extracted from prefect.py so that validation can run without importing
the Prefect engine — needed by FastAPI routes and the React Flow editor.

Provides:
- compute_parallel_levels: BFS level grouping with cycle detection (Kahn's algorithm)
- validate_workflow_def: Structural validation (required fields, upstream refs, node types, config)
"""

from collections.abc import Mapping

from src.domain.entities.shared import JsonValue
from src.domain.entities.workflow import WorkflowDef, WorkflowTaskDef

from .node_config_fields import (
    FieldType,
    get_enricher_metric_names,
    get_node_config_fields,
)
from .node_registry import get_node

# Type mapping from field_type strings to Python types for isinstance() checks.
_FIELD_TYPE_MAP: dict[FieldType, type | tuple[type, ...]] = {
    "string": str,
    "number": (int, float),
    "boolean": bool,
    "select": str,
}


def compute_parallel_levels(
    tasks: list[WorkflowTaskDef],
) -> list[list[WorkflowTaskDef]]:
    """Group tasks into parallel execution levels using BFS topological sort.

    Level 0: tasks with no dependencies (can all run concurrently)
    Level N: tasks whose ALL dependencies are in levels < N

    Uses Kahn's algorithm — iteratively peels off zero-in-degree nodes.

    Returns:
        List of levels, each containing tasks that can execute in parallel.

    Raises:
        ValueError: If the task graph contains a cycle.
    """
    task_by_id = {t.id: t for t in tasks}

    # Build adjacency list (parent → children) for efficient traversal
    children: dict[str, list[str]] = {t.id: [] for t in tasks}
    in_degree: dict[str, int] = {}
    for t in tasks:
        in_degree[t.id] = len(t.upstream)
        for parent_id in t.upstream:
            children[parent_id].append(t.id)

    # Start with zero-dependency tasks
    current_ids = [t.id for t in tasks if in_degree[t.id] == 0]
    levels: list[list[WorkflowTaskDef]] = []
    visited = 0

    while current_ids:
        levels.append([task_by_id[tid] for tid in current_ids])
        visited += len(current_ids)
        next_ids: list[str] = []
        for tid in current_ids:
            for child_id in children[tid]:
                in_degree[child_id] -= 1
                if in_degree[child_id] == 0:
                    next_ids.append(child_id)
        current_ids = next_ids

    if visited != len(tasks):
        unvisited = {t.id for t in tasks} - {t.id for level in levels for t in level}
        raise ValueError(f"Cycle detected in workflow: {sorted(unvisited)}")

    return levels


def _validate_node_config(
    node_type: str, config: Mapping[str, JsonValue], task_id: str
) -> None:
    """Validate that a node's config contains all required keys with correct types.

    Derives required keys and type checks from the rich config field registry
    in node_config_fields.py. Nodes with no fields pass unconditionally.

    Raises:
        ValueError: If required config keys are missing, have wrong types,
            or are empty strings when a non-empty string is required.
    """
    fields = get_node_config_fields().get(node_type, ())

    # Check for missing required keys
    required_keys = [f.key for f in fields if f.required]
    missing = [k for k in required_keys if k not in config]
    if missing:
        raise ValueError(
            f"Task '{task_id}' (type '{node_type}') missing required config: {missing}"
        )

    # Validate value types for required keys
    required_type_map = {
        f.key: _FIELD_TYPE_MAP.get(f.field_type, str) for f in fields if f.required
    }
    for key, expected_type in required_type_map.items():
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


def validate_workflow_def(workflow_def: WorkflowDef) -> None:
    """Validate workflow definition structure before execution.

    Catches structural errors early — before any expensive I/O operations run.
    Checks for: non-empty tasks, valid upstream references, and resolvable node types.

    Raises:
        ValueError: If the workflow definition is structurally invalid.
    """
    if not workflow_def.tasks:
        raise ValueError("Workflow has no tasks")

    task_ids = {task.id for task in workflow_def.tasks}
    if len(task_ids) != len(workflow_def.tasks):
        from collections import Counter

        counts = Counter(t.id for t in workflow_def.tasks)
        dupes = [tid for tid, n in counts.items() if n > 1]
        raise ValueError(f"Duplicate task IDs: {dupes}")

    # Validate upstream references point to existing tasks
    for task_def in workflow_def.tasks:
        for upstream_id in task_def.upstream:
            if upstream_id not in task_ids:
                raise ValueError(
                    f"Task '{task_def.id}' references unknown upstream '{upstream_id}'. Available: {sorted(task_ids)}"
                )

    # Validate node types are resolvable in the registry
    for task_def in workflow_def.tasks:
        try:
            get_node(task_def.type)
        except KeyError:
            raise ValueError(
                f"Task '{task_def.id}' has unknown node type '{task_def.type}'"
            ) from None

    # Validate node config required keys per node type
    for task_def in workflow_def.tasks:
        _validate_node_config(task_def.type, task_def.config, task_id=task_def.id)


# --- Connector pre-flight validation ---


def extract_required_connectors(workflow_def: WorkflowDef) -> set[str]:
    """Extract connector names required by workflow nodes.

    Sources:
    - Explicit ``config["connector"]`` on source/destination nodes
    - Implicit from enricher node types (e.g., ``enricher.spotify`` → "spotify")
    """
    connectors: set[str] = set()
    for task_def in workflow_def.tasks:
        # Explicit connector in config (source.playlist, destination.*)
        if connector := task_def.config.get("connector"):
            connectors.add(str(connector))

        # Registry-declared connector requirements
        _, metadata = get_node(task_def.type)
        if node_connectors := metadata.get("required_connectors"):
            connectors.update(node_connectors)

    return connectors


def validate_connector_availability(
    required: set[str], available: list[str]
) -> list[str]:
    """Return sorted list of missing connectors (empty = all available)."""
    available_set = set(available)
    missing = sorted(required - available_set)
    return missing


# Derived from the canonical ENRICHER_METRIC_DEFS in node_config_fields.py.
_ENRICHER_METRICS: dict[str, frozenset[str]] = get_enricher_metric_names()

# Node types whose required enricher is derived from config["metric_name"]
# via the ENRICHER_METRIC_DEFS lookup (scalar-metric consumers).
_METRIC_CONSUMER_TYPES: frozenset[str] = frozenset({
    "filter.by_metric",
    "sorter.by_metric",
})

# Node types whose required enricher is fixed (not metric-name-derived).
# The consumer needs this enricher upstream regardless of config.
_ENRICHER_CONSUMER_MAP: dict[str, str] = {
    "filter.by_preference": "enricher.preferences",
    "sorter.by_preference": "enricher.preferences",
    "filter.by_tag": "enricher.tags",
    "filter.by_tag_namespace": "enricher.tags",
}


def _validate_enrichment_dependencies(
    workflow_def: WorkflowDef,
) -> list[dict[str, str]]:
    """Walk the DAG and warn when filter/sorter nodes have no upstream enricher.

    Covers two consumer families:
    - Metric consumers (filter.by_metric, sorter.by_metric): required enricher
      is derived from config["metric_name"] via ENRICHER_METRIC_DEFS.
    - Enricher consumers (filter.by_preference, filter.by_tag, ...): required
      enricher is fixed per consumer node type.

    Returns structured warnings (not errors) — the workflow can still run,
    but the sort/filter will produce meaningless results.
    """
    warnings: list[dict[str, str]] = []
    task_by_id = {task.id: task for task in workflow_def.tasks}

    def _collect_upstream_enricher_types(
        task_id: str, visited: set[str] | None = None
    ) -> set[str]:
        """Recursively collect all enricher node types upstream of a task."""
        if visited is None:
            visited = set()
        if task_id in visited:
            return set()
        visited.add(task_id)
        task = task_by_id.get(task_id)
        if not task:
            return set()
        enricher_types: set[str] = set()
        if task.type.startswith("enricher."):
            enricher_types.add(task.type)
        for upstream_id in task.upstream:
            enricher_types |= _collect_upstream_enricher_types(upstream_id, visited)
        return enricher_types

    for task_def in workflow_def.tasks:
        if task_def.type in _METRIC_CONSUMER_TYPES:
            metric_name = task_def.config.get("metric_name")
            if not metric_name:
                continue

            upstream_enrichers = _collect_upstream_enricher_types(task_def.id)
            available_metrics: set[str] = set()
            for enricher_type in upstream_enrichers:
                available_metrics |= _ENRICHER_METRICS.get(
                    enricher_type, frozenset[str]()
                )

            if metric_name not in available_metrics:
                warnings.append({
                    "task_id": task_def.id,
                    "field": "config.metric_name",
                    "severity": "warning",
                    "message": (
                        f"'{metric_name}' has no upstream enricher — "
                        f"sort/filter will have no data. "
                        f"Available metrics from upstream: {sorted(available_metrics) or 'none'}"
                    ),
                })
        elif required_enricher := _ENRICHER_CONSUMER_MAP.get(task_def.type):
            upstream_enrichers = _collect_upstream_enricher_types(task_def.id)
            if required_enricher not in upstream_enrichers:
                warnings.append({
                    "task_id": task_def.id,
                    "field": "type",
                    "severity": "warning",
                    "message": (
                        f"'{task_def.type}' requires upstream '{required_enricher}' — "
                        f"sort/filter will have no data. "
                        f"Upstream enrichers: {sorted(upstream_enrichers) or 'none'}"
                    ),
                })

    return warnings


def validate_workflow_def_detailed(workflow_def: WorkflowDef) -> list[dict[str, str]]:
    """Validate workflow definition returning structured error details.

    Wraps validate_workflow_def() logic, catching ValueError and converting
    to structured [{task_id, field, message}] dicts. Empty list = valid.
    """
    errors: list[dict[str, str]] = []

    if not workflow_def.tasks:
        errors.append({
            "task_id": "",
            "field": "tasks",
            "message": "Workflow has no tasks",
        })
        return errors

    task_ids = {task.id for task in workflow_def.tasks}

    for task_def in workflow_def.tasks:
        errors.extend(
            {
                "task_id": task_def.id,
                "field": "upstream",
                "message": f"References unknown upstream '{upstream_id}'",
            }
            for upstream_id in task_def.upstream
            if upstream_id not in task_ids
        )

    for task_def in workflow_def.tasks:
        try:
            get_node(task_def.type)
        except KeyError:
            errors.append({
                "task_id": task_def.id,
                "field": "type",
                "message": f"Unknown node type '{task_def.type}'",
            })
            continue

        try:
            _validate_node_config(task_def.type, task_def.config, task_id=task_def.id)
        except ValueError as e:
            errors.append({
                "task_id": task_def.id,
                "field": "config",
                "message": str(e),
            })

    # Check for cycles
    try:
        compute_parallel_levels(workflow_def.tasks)
    except ValueError as e:
        errors.append({"task_id": "", "field": "tasks", "message": str(e)})

    # Enrichment dependency warnings (non-blocking but surfaced to the user)
    errors.extend(_validate_enrichment_dependencies(workflow_def))

    return errors


def is_validation_error(item: dict[str, str]) -> bool:
    """True if a validation result item is an error (not a warning)."""
    return item.get("severity") != "warning"


class ConnectorNotAvailableError(Exception):
    """Raised when a workflow requires connectors that are not configured."""

    def __init__(self, missing_connectors: list[str]) -> None:
        self.missing_connectors = missing_connectors
        super().__init__(
            f"Missing required connectors: {', '.join(missing_connectors)}"
        )
