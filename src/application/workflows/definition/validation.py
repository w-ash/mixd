"""Workflow definition validation.

Extracted from the executor so that validation can run without importing
the execution engine — needed by FastAPI routes and the React Flow editor.
The pure DAG level/cycle algorithm (``compute_parallel_levels``) lives in the
domain beside ``WorkflowTaskDef``; this module imports it for cycle detection.

Both entry points share one rule set via ``_collect_validation_items`` so the
editor's verdict can never diverge from the save path:
- validate_workflow_def: raises on the first blocking error (guards save/execute)
- validate_workflow_def_detailed: returns all errors + warnings (React Flow editor)
"""

from collections import Counter
from collections.abc import Mapping
from typing import NamedTuple

from src.application.workflows.nodes.config_accessors import cfg_int, cfg_str_list
from src.application.workflows.nodes.config_fields import (
    DEFAULT_PLAY_HISTORY_METRICS,
    FieldType,
    get_enricher_metric_names,
    get_node_config_fields,
)
from src.application.workflows.nodes.registry import get_node
from src.domain.entities.shared import JsonValue
from src.domain.entities.workflow import (
    WorkflowDef,
    WorkflowTaskDef,
    compute_parallel_levels,
)

# Type mapping from field_type strings to Python types for isinstance() checks.
_FIELD_TYPE_MAP: dict[FieldType, type | tuple[type, ...]] = {
    "string": str,
    "number": (int, float),
    "boolean": bool,
    "select": str,
}


def _validate_node_config(
    node_type: str, config: Mapping[str, JsonValue], task_id: str
) -> None:
    """Validate that a node's config contains all required keys with correct types.

    Derives required keys and type checks from the rich config field registry
    in nodes/config_fields.py. Nodes with no fields pass unconditionally.

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


def _check_primary_input(task_def: WorkflowTaskDef) -> str | None:
    """Return an error message if ``primary_input`` is set but not an upstream.

    The executor uses ``config["primary_input"]`` to pick which upstream feeds a
    multi-input node; when it names a task that isn't actually upstream it
    silently falls back to ``upstream[0]``, producing plausible-but-wrong output.
    """
    primary = task_def.config.get("primary_input")
    if primary is None:
        return None
    if primary not in task_def.upstream:
        return (
            f"Task '{task_def.id}' sets primary_input '{primary}', which is not "
            f"one of its upstream tasks {sorted(task_def.upstream)}"
        )
    return None


def _check_source_placement(task_def: WorkflowTaskDef, category: str) -> str | None:
    """Return an error message if a non-source node has no upstream.

    A node with no upstream lands at DAG level 0; only source nodes can produce
    data from nothing. A filter/sorter/destination placed there passes the
    topological check but empties or errors at runtime.
    """
    if category != "source" and not task_def.upstream:
        return (
            f"Task '{task_def.id}' (type '{task_def.type}', category '{category}') "
            f"has no upstream — only source nodes may run without input"
        )
    return None


def _find_result_key_problems(
    tasks: list[WorkflowTaskDef],
) -> list[tuple[str, str]]:
    """Find ``result_key`` collisions and duplicates as ``(task_id, message)``.

    The executor stores each result twice — under the task id and, if set, under
    ``result_key`` as an alias. A ``result_key`` equal to *another* task's id
    silently overwrites that task's result; two tasks sharing a ``result_key``
    collide the same way. A ``result_key`` equal to the task's *own* id is a
    harmless self-overwrite and is allowed.
    """
    task_ids = {t.id for t in tasks}
    problems: list[tuple[str, str]] = []
    seen: dict[str, str] = {}  # result_key -> first task id that declared it
    for t in tasks:
        if not t.result_key:
            continue
        # At most one problem per task: a key colliding with another task's id is
        # reported as a collision, otherwise as a duplicate. Either way record the
        # first declarer so the blocking and detailed validators agree on count.
        if t.result_key in task_ids and t.result_key != t.id:
            problems.append((
                t.id,
                f"Task '{t.id}' result_key '{t.result_key}' collides with the id of "
                f"another task — it would overwrite that task's result",
            ))
        elif t.result_key in seen:
            problems.append((
                t.id,
                f"Task '{t.id}' result_key '{t.result_key}' duplicates the one on "
                f"task '{seen[t.result_key]}'",
            ))
        seen.setdefault(t.result_key, t.id)
    return problems


def _collect_validation_items(workflow_def: WorkflowDef) -> list[dict[str, str]]:
    """Single source of truth for workflow-definition validation.

    Returns structured ``{task_id, field, message, [severity]}`` items in
    precedence order — blocking errors first, non-blocking warnings last.
    ``validate_workflow_def`` raises on the first *error* item; the editor's
    ``validate_workflow_def_detailed`` returns the whole list. Keeping both
    surfaces behind one collector is what stops them diverging: before this,
    only the blocking path caught duplicate ids and only the detailed path
    caught cycles, so a cyclic workflow could be saved and a duplicate-id
    workflow passed the editor.
    """
    if not workflow_def.tasks:
        return [{"task_id": "", "field": "tasks", "message": "Workflow has no tasks"}]

    items: list[dict[str, str]] = []
    task_ids = {task.id for task in workflow_def.tasks}

    # Duplicate task ids must come first: the DAG topology (upstream refs,
    # cycle detection) is undefined until ids uniquely address a task.
    duplicate_ids = sorted(
        tid for tid, n in Counter(t.id for t in workflow_def.tasks).items() if n > 1
    )
    if duplicate_ids:
        items.append({
            "task_id": "",
            "field": "tasks",
            "message": f"Duplicate task IDs: {duplicate_ids}",
        })

    # Upstream references must point to existing tasks. Track well-formedness so
    # cycle detection — which assumes every upstream resolves — only runs when
    # the graph is addressable (a dangling upstream would otherwise KeyError).
    upstream_well_formed = True
    for task_def in workflow_def.tasks:
        for upstream_id in task_def.upstream:
            if upstream_id not in task_ids:
                upstream_well_formed = False
                items.append({
                    "task_id": task_def.id,
                    "field": "upstream",
                    "message": (
                        f"Task '{task_def.id}' references unknown upstream "
                        f"'{upstream_id}'. Available: {sorted(task_ids)}"
                    ),
                })

    # Per-task: type resolvability, config completeness, and the silent-wrong
    # guards (primary_input that isn't an upstream, non-source node with no input).
    for task_def in workflow_def.tasks:
        try:
            _, metadata = get_node(task_def.type)
        except KeyError:
            items.append({
                "task_id": task_def.id,
                "field": "type",
                "message": f"Task '{task_def.id}' has unknown node type '{task_def.type}'",
            })
            continue
        try:
            _validate_node_config(task_def.type, task_def.config, task_id=task_def.id)
        except ValueError as e:
            items.append({"task_id": task_def.id, "field": "config", "message": str(e)})
        for error_field, message in (
            ("config.primary_input", _check_primary_input(task_def)),
            ("upstream", _check_source_placement(task_def, metadata["category"])),
        ):
            if message:
                items.append({
                    "task_id": task_def.id,
                    "field": error_field,
                    "message": message,
                })

    # Reject result_key aliases that collide with a task id or duplicate another.
    items.extend(
        {"task_id": task_id, "field": "result_key", "message": message}
        for task_id, message in _find_result_key_problems(workflow_def.tasks)
    )

    # Cycle detection — only meaningful once ids are unique and every upstream
    # resolves; otherwise the graph is ambiguous and the cycle message would
    # mislead (and the algorithm would KeyError on a dangling reference).
    if not duplicate_ids and upstream_well_formed:
        try:
            compute_parallel_levels(workflow_def.tasks)
        except ValueError as e:
            items.append({"task_id": "", "field": "tasks", "message": str(e)})

    # Enrichment dependency warnings (non-blocking, surfaced to the editor).
    items.extend(_validate_enrichment_dependencies(workflow_def))

    return items


def validate_workflow_def(workflow_def: WorkflowDef) -> None:
    """Validate workflow definition structure before execution.

    Catches structural errors early — before any expensive I/O operations run.
    Checks for: non-empty tasks, no duplicate ids, valid upstream references,
    resolvable node types, config completeness, primary_input/result_key
    correctness, correct source placement, and acyclicity. Raises on the first
    blocking error; non-blocking warnings are ignored on this path.

    Raises:
        ValueError: If the workflow definition is structurally invalid.
    """
    for item in _collect_validation_items(workflow_def):
        if is_validation_error(item):
            raise ValueError(item["message"])


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
    return sorted(required - available_set)


# Derived from the canonical ENRICHER_METRIC_DEFS in nodes/config_fields.py.
# Lazily computed (not at import) so importing this module — e.g. by API
# middleware at boot — doesn't eagerly walk the node config registry.
_enricher_metrics_cache: dict[str, frozenset[str]] | None = None


def _enricher_metrics() -> dict[str, frozenset[str]]:
    """Resolve enricher type → metric-names on first use, then cache."""
    global _enricher_metrics_cache
    if _enricher_metrics_cache is None:
        _enricher_metrics_cache = get_enricher_metric_names()
    return _enricher_metrics_cache


# The one enricher whose emitted metrics depend on its own ``metrics`` config
# rather than being fixed by type — so validation must read config, not just
# capability, when reasoning about what it produces.
_PLAY_HISTORY_ENRICHER = "enricher.play_history"


# Node types whose required enricher is derived from config["metric_name"]
# via the ENRICHER_METRIC_DEFS lookup (scalar-metric consumers).
_METRIC_CONSUMER_TYPES: frozenset[str] = frozenset({
    "filter.by_metric",
    "sorter.by_metric",
})


class _EnricherRequirement(NamedTuple):
    """A consumer node's fixed enricher dependency.

    ``metric`` is set only when the enricher emits it *conditionally on its own
    config* (play_history's ``first_played_dates``), so the enricher type being
    upstream isn't enough — the upstream task must be configured to emit it.
    ``None`` means the enricher always produces what the consumer needs.
    """

    enricher_type: str
    metric: str | None = None


# Node types whose required enricher is fixed (not metric-name-derived).
_ENRICHER_CONSUMER_MAP: dict[str, _EnricherRequirement] = {
    "filter.by_preference": _EnricherRequirement("enricher.preferences"),
    "sorter.by_preference": _EnricherRequirement("enricher.preferences"),
    "filter.by_tag": _EnricherRequirement("enricher.tags"),
    "filter.by_tag_namespace": _EnricherRequirement("enricher.tags"),
    "filter.by_first_played_date": _EnricherRequirement(
        "enricher.play_history", metric="first_played_dates"
    ),
}


def _enricher_emitted_metrics(enricher_task: WorkflowTaskDef) -> set[str]:
    """The metrics an enricher task is *configured* to emit (not just capable of).

    ``enricher.play_history`` emits only the metrics named in its ``metrics``
    config (defaulting to ``DEFAULT_PLAY_HISTORY_METRICS`` when unset), so its
    output depends on config, not just type. Every other enricher has no
    ``metrics`` config and always emits its full capability set.

    This is the config-aware view both consumer checks below rely on: "this
    enricher *can* emit X" (capability) is not "this enricher *will* emit X".
    """
    if enricher_task.type == _PLAY_HISTORY_ENRICHER:
        configured = cfg_str_list(enricher_task.config, "metrics")
        return set(configured or DEFAULT_PLAY_HISTORY_METRICS)
    return set(_enricher_metrics().get(enricher_task.type, frozenset[str]()))


def _enricher_emits(enricher_task: WorkflowTaskDef, metric: str) -> bool:
    """True if an enricher task is configured to emit ``metric``.

    play_history with default/empty config does NOT satisfy a consumer that
    needs a non-default metric (e.g. ``first_played_dates``).
    """
    return metric in _enricher_emitted_metrics(enricher_task)


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

    def _collect_upstream_enrichers(
        task_id: str, visited: set[str] | None = None
    ) -> list[WorkflowTaskDef]:
        """Recursively collect all enricher tasks upstream of a task.

        Returns tasks (not just types) so the caller can inspect a play_history
        enricher's ``metrics`` config, not only its presence.
        """
        if visited is None:
            visited = set()
        if task_id in visited:
            return []
        visited.add(task_id)
        task = task_by_id.get(task_id)
        if not task:
            return []
        enrichers: list[WorkflowTaskDef] = []
        if task.type.startswith("enricher."):
            enrichers.append(task)
        for upstream_id in task.upstream:
            enrichers.extend(_collect_upstream_enrichers(upstream_id, visited))
        return enrichers

    for task_def in workflow_def.tasks:
        if task_def.type in _METRIC_CONSUMER_TYPES:
            metric_name = task_def.config.get("metric_name")
            if not metric_name:
                continue

            # Config-aware: a play_history enricher emits only the metrics it's
            # configured for, so union each upstream's *emitted* set, not the
            # capability set — otherwise a default-config enricher certifies a
            # consumer (e.g. metric_name="period_plays") that produces nothing.
            available_metrics: set[str] = set()
            for enricher in _collect_upstream_enrichers(task_def.id):
                available_metrics |= _enricher_emitted_metrics(enricher)

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
        elif requirement := _ENRICHER_CONSUMER_MAP.get(task_def.type):
            upstream = _collect_upstream_enrichers(task_def.id)
            matching = [e for e in upstream if e.type == requirement.enricher_type]
            metric_ok = requirement.metric is None or any(
                _enricher_emits(e, requirement.metric) for e in matching
            )
            if not (matching and metric_ok):
                need = (
                    f"upstream '{requirement.enricher_type}'"
                    if requirement.metric is None
                    else f"'{requirement.metric}' from upstream '{requirement.enricher_type}'"
                )
                upstream_types = sorted({e.type for e in upstream})
                warnings.append({
                    "task_id": task_def.id,
                    "field": "type",
                    "severity": "warning",
                    "message": (
                        f"'{task_def.type}' requires {need} — "
                        f"sort/filter will have no data. "
                        f"Upstream enrichers: {upstream_types or 'none'}"
                    ),
                })

    # An enricher.play_history ``period_days`` only takes effect when
    # ``period_plays`` is among its metrics (the runtime gates the window on it).
    # Set without it, the day window is silently ignored — warn rather than let
    # the user assume a recency window that never applies.
    for task_def in workflow_def.tasks:
        if task_def.type != _PLAY_HISTORY_ENRICHER:
            continue
        if cfg_int(task_def.config, "period_days") and (
            "period_plays" not in _enricher_emitted_metrics(task_def)
        ):
            warnings.append({
                "task_id": task_def.id,
                "field": "config.period_days",
                "severity": "warning",
                "message": (
                    "'period_days' is set but 'period_plays' is not in this "
                    "enricher's metrics — the day window is ignored. Add "
                    "'period_plays' to metrics to apply it."
                ),
            })

    return warnings


def validate_workflow_def_detailed(workflow_def: WorkflowDef) -> list[dict[str, str]]:
    """Validate workflow definition returning structured error details.

    Returns the full ``[{task_id, field, message, [severity]}]`` list from the
    shared collector — blocking errors plus non-blocking enrichment warnings.
    Empty list = valid (the editor distinguishes errors from warnings via
    ``is_validation_error``). Shares the exact rule set with
    ``validate_workflow_def`` so the editor's verdict matches the save path.
    """
    return _collect_validation_items(workflow_def)


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
