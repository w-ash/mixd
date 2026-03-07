"""Workflow definition validation and DAG utilities.

Extracted from prefect.py so that validation can run without importing
the Prefect engine — needed by FastAPI routes and the React Flow editor.

Provides:
- topological_sort: Orders tasks by dependencies (3-color DFS with cycle detection)
- validate_workflow_def: Structural validation (required fields, upstream refs, node types, config)
"""

# pyright: reportExplicitAny=false, reportAny=false

from typing import Any

from src.domain.entities.workflow import WorkflowDef, WorkflowTaskDef

from .node_registry import get_node

# Required config schema per node type — keys that must be present and their
# expected types.  Nodes not listed here have no mandatory keys.
# Values are single types or tuples of types (for isinstance() checks).
_NODE_CONFIG_SCHEMA: dict[str, dict[str, type | tuple[type, ...]]] = {
    "source.playlist": {"playlist_id": str},
    "filter.by_metric": {"metric_name": str},
    "filter.by_tracks": {"exclusion_source": str},
    "filter.by_artists": {"exclusion_source": str},
    "filter.by_liked_status": {"service": str},
    "selector.percentage": {"percentage": (int, float)},
    "destination.create_playlist": {"name": str},
    "destination.update_playlist": {"playlist_id": str},
}


def topological_sort(tasks: list[WorkflowTaskDef]) -> list[WorkflowTaskDef]:
    """Orders tasks by dependencies to ensure upstream tasks execute first.

    Uses 3-color DFS to detect cycles: unvisited -> visiting (gray) -> visited (black).
    A back-edge to a gray node means a cycle exists.
    """
    graph = {task.id: task.upstream for task in tasks}

    visited: set[str] = set()
    visiting: set[str] = set()
    result: list[WorkflowTaskDef] = []

    task_by_id = {task.id: task for task in tasks}

    def visit(node_id: str) -> None:
        if node_id in visited:
            return
        if node_id in visiting:
            raise ValueError(f"Cycle detected in workflow: {node_id}")
        visiting.add(node_id)
        for dep in graph.get(node_id, []):  # fallback for pre-validation callers
            visit(dep)
        visiting.discard(node_id)
        visited.add(node_id)
        if node_id in task_by_id:
            result.append(task_by_id[node_id])

    for task_id in graph:
        visit(task_id)

    return result


def _validate_node_config(node_type: str, config: dict[str, Any], task_id: str) -> None:
    """Validate that a node's config contains all required keys with correct types.

    Called during workflow definition validation to catch config errors
    before any expensive I/O operations run. Nodes not in _REQUIRED_CONFIG
    have no mandatory keys and pass validation unconditionally.

    Raises:
        ValueError: If required config keys are missing, have wrong types,
            or are empty strings when a non-empty string is required.
    """
    schema = _NODE_CONFIG_SCHEMA.get(node_type, {})
    missing = [k for k in schema if k not in config]
    if missing:
        raise ValueError(
            f"Task '{task_id}' (type '{node_type}') missing required config: {missing}"
        )

    # Validate value types for required keys
    for key, expected_type in schema.items():
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
        topological_sort(workflow_def.tasks)
    except ValueError as e:
        errors.append({"task_id": "", "field": "tasks", "message": str(e)})

    return errors


def get_node_config_schema() -> dict[str, dict[str, type | tuple[type, ...]]]:
    """Public accessor for node config schema (used by API node catalog endpoint)."""
    return _NODE_CONFIG_SCHEMA


class ConnectorNotAvailableError(Exception):
    """Raised when a workflow requires connectors that are not configured."""

    def __init__(self, missing_connectors: list[str]) -> None:
        self.missing_connectors = missing_connectors
        super().__init__(
            f"Missing required connectors: {', '.join(missing_connectors)}"
        )
