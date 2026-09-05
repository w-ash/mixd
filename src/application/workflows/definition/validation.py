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

from src.application.workflows.nodes.config_accessors import cfg_str_list
from src.application.workflows.nodes.config_fields import (
    ConfigFieldDef,
    FieldType,
    apply_declared_defaults,
    format_bound,
    get_enricher_metric_names,
    get_node_config_fields,
    is_unset,
)
from src.application.workflows.nodes.registry import get_node, list_nodes
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
    "multi_select": list,
    "task_ref": str,
}


def _type_name(expected_type: type | tuple[type, ...]) -> str:
    if isinstance(expected_type, type):
        return expected_type.__name__
    return " | ".join(t.__name__ for t in expected_type)


def _constraint_warning(
    field: ConfigFieldDef, value: JsonValue, task_id: str
) -> str | None:
    """Return a warning when a present value falls outside the field's constraints.

    Covers ``options`` membership for select (the value) and multi_select
    (every element) and ``min``/``max`` for number fields. Empty selects count
    as unset — the editor's "leave empty" — so they pass. Values of the wrong
    type are left to the error check; a constraint cannot be judged on them.
    """
    key = field.key
    if field.field_type in ("select", "multi_select") and field.options:
        allowed = [option.value for option in field.options]
        if field.field_type == "select":
            offending = (
                [value]
                if isinstance(value, str) and value and value not in allowed
                else []
            )
        else:
            offending = (
                [v for v in value if not isinstance(v, str) or v not in allowed]
                if isinstance(value, list)
                else []
            )
        if offending:
            return (
                f"Task '{task_id}' config key '{key}' should be one of {allowed}, "
                f"got {offending!r}"
            )
    if field.field_type == "number" and isinstance(value, (int, float)):
        if isinstance(value, bool):
            return None
        below = field.min is not None and value < field.min
        above = field.max is not None and value > field.max
        if below or above:
            low = format_bound(field.min) if field.min is not None else "-inf"
            high = format_bound(field.max) if field.max is not None else "inf"
            return (
                f"Task '{task_id}' config key '{key}' should be between "
                f"{low} and {high}, got {value!r}"
            )
    return None


def _check_node_config(
    node_type: str, config: Mapping[str, JsonValue], task_id: str
) -> tuple[str | None, list[tuple[str, str]]]:
    """Return ``(first_error, [(config_key, warning), ...])`` for a task's config.

    Derives every rule from the rich config field registry in
    nodes/config_fields.py. Errors — a missing required key, a required value
    of the wrong type, an empty required string — block save and execute;
    only the first is reported, so the blocking and detailed validators agree
    on item count. Warnings cover an optional value of the wrong type (the
    runtime accessor falls back to the default, so the value is ignored, not
    fatal) plus ``options`` membership and ``min``/``max`` for every declared
    field present in config; the workflow still runs.

    A value ``is_unset`` (``null``, ``[]`` on a multi_select) counts as absent
    here exactly as ``apply_declared_defaults`` drops it at run time: it is
    "missing" when required and skipped otherwise, never a type problem.
    """
    fields = get_node_config_fields().get(node_type, ())
    present = [f for f in fields if f.key in config and not is_unset(f, config[f.key])]

    missing = [f.key for f in fields if f.required and f not in present]
    if missing:
        return (
            f"Task '{task_id}' (type '{node_type}') missing required config: {missing}",
            [],
        )

    warnings: list[tuple[str, str]] = []
    for field in present:
        key = field.key
        value = config[key]
        expected_type = _FIELD_TYPE_MAP[field.field_type]
        if not isinstance(value, expected_type):
            problem = (
                f"Task '{task_id}' config key '{key}' must be "
                f"{_type_name(expected_type)}, got {type(value).__name__}: {value!r}"
            )
            if field.required:
                return problem, []
            warnings.append((key, f"{problem} — the value is ignored"))
            continue
        if field.required and isinstance(value, str) and not value.strip():
            return f"Task '{task_id}' config key '{key}' must not be empty", []
        if warning := _constraint_warning(field, value, task_id):
            warnings.append((key, warning))
    return None, warnings


def _check_task_refs(
    task_def: WorkflowTaskDef, fields: tuple[ConfigFieldDef, ...]
) -> list[tuple[str, str]]:
    """Return ``(config_key, message)`` for each task_ref that is not an upstream.

    A task_ref names another task whose result this node reads
    (``primary_input``, ``exclusion_source``). The executor only has results for
    declared upstreams: a ``primary_input`` naming anything else silently falls
    back to ``upstream[0]``, and an ``exclusion_source`` fails at runtime.
    """
    problems: list[tuple[str, str]] = []
    for field in fields:
        if field.field_type != "task_ref" or field.key not in task_def.config:
            continue
        ref = task_def.config[field.key]
        if ref is None or ref in task_def.upstream:
            continue
        problems.append((
            field.key,
            (
                f"Task '{task_def.id}' sets {field.key} '{ref}', which is not "
                f"one of its upstream tasks {sorted(task_def.upstream)}"
            ),
        ))
    return problems


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
                (
                    f"Task '{t.id}' result_key '{t.result_key}' collides with the id of "
                    f"another task — it would overwrite that task's result"
                ),
            ))
        elif t.result_key in seen:
            problems.append((
                t.id,
                (
                    f"Task '{t.id}' result_key '{t.result_key}' duplicates the one on "
                    f"task '{seen[t.result_key]}'"
                ),
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
    # guards (task_ref that isn't an upstream, non-source node with no input).
    all_fields = get_node_config_fields()
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
        config_error, config_warnings = _check_node_config(
            task_def.type, task_def.config, task_def.id
        )
        if config_error:
            items.append({
                "task_id": task_def.id,
                "field": "config",
                "message": config_error,
            })
        items.extend(
            {
                "task_id": task_def.id,
                "field": f"config.{key}",
                "message": message,
                "severity": "warning",
            }
            for key, message in config_warnings
        )
        items.extend(
            {
                "task_id": task_def.id,
                "field": f"config.{key}",
                "message": message,
            }
            for key, message in _check_task_refs(
                task_def, all_fields.get(task_def.type, ())
            )
        )
        if placement := _check_source_placement(task_def, metadata["category"]):
            items.append({
                "task_id": task_def.id,
                "field": "upstream",
                "message": placement,
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
    resolvable node types, config completeness, task_ref/result_key
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


def _enricher_emitted_metrics(enricher_task: WorkflowTaskDef) -> set[str]:
    """The metrics an enricher task is *configured* to emit (not just capable of).

    An enricher whose registry metadata declares ``emits_metrics_from_config``
    emits only the metrics named under that config key, so its output depends
    on config, not just type. The key is read through
    ``apply_declared_defaults`` — the same call the executor makes — so an
    omitted, ``null`` or empty list falls back to the declared default here
    exactly as it does at run time. Every other enricher always emits its
    full capability set.

    This is the config-aware view the consumer checks below rely on: "this
    enricher *can* emit X" (capability) is not "this enricher *will* emit X".
    """
    metadata = list_nodes().get(enricher_task.type)
    key = metadata.get("emits_metrics_from_config") if metadata else None
    if key is not None:
        config = apply_declared_defaults(enricher_task.type, enricher_task.config)
        return set(cfg_str_list(config, key))
    return set(get_enricher_metric_names().get(enricher_task.type, frozenset[str]()))


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

    Every rule is driven by registry metadata (``NodeMetadata``), so a new
    consumer or enricher declares its dependency at registration and needs no
    edit here. Three rules:
    - ``metric_from_config``: the required enricher is whichever upstream is
      configured to emit the metric named by that config key.
    - ``requires_enricher`` (+ optional ``requires_metric``): the enricher type
      is fixed per consumer node type.
    - ``metric_config_corequisites``: an enricher config key that only takes
      effect when a given metric is among its emitted set.

    Returns structured warnings (not errors) — the workflow can still run,
    but the sort/filter will produce meaningless results.
    """
    warnings: list[dict[str, str]] = []
    task_by_id = {task.id: task for task in workflow_def.tasks}
    registered = list_nodes()

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
        # The registry owns the node's category — never re-derive it from the id.
        # Unknown types were already reported above; skip them here.
        if task.type in registered and registered[task.type]["category"] == "enricher":
            enrichers.append(task)
        for upstream_id in task.upstream:
            enrichers.extend(_collect_upstream_enrichers(upstream_id, visited))
        return enrichers

    for task_def in workflow_def.tasks:
        metadata = registered.get(task_def.type)
        if metadata is None:
            continue

        if metric_key := metadata.get("metric_from_config"):
            metric_name = task_def.config.get(metric_key)
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
                    "field": f"config.{metric_key}",
                    "severity": "warning",
                    "message": (
                        f"'{metric_name}' has no upstream enricher — "
                        f"sort/filter will have no data. "
                        f"Available metrics from upstream: {sorted(available_metrics) or 'none'}"
                    ),
                })
        elif enricher_type := metadata.get("requires_enricher"):
            # ``requires_metric`` is set only when the enricher emits it
            # conditionally on its own config, so the enricher type being
            # upstream isn't enough — the task must be configured to emit it.
            metric = metadata.get("requires_metric")
            upstream = _collect_upstream_enrichers(task_def.id)
            matching = [e for e in upstream if e.type == enricher_type]
            metric_ok = metric is None or any(
                _enricher_emits(e, metric) for e in matching
            )
            if not (matching and metric_ok):
                need = (
                    f"upstream '{enricher_type}'"
                    if metric is None
                    else f"'{metric}' from upstream '{enricher_type}'"
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

        # A corequisite config key (play_history's ``period_days``) only takes
        # effect when its metric is among the enricher's emitted set. Set
        # without it, the key is silently ignored — warn rather than let the
        # user assume a window that never applies.
        for key, metric in metadata.get("metric_config_corequisites", {}).items():
            if task_def.config.get(key) and (
                metric not in _enricher_emitted_metrics(task_def)
            ):
                emits_key = metadata.get("emits_metrics_from_config", "metrics")
                warnings.append({
                    "task_id": task_def.id,
                    "field": f"config.{key}",
                    "severity": "warning",
                    "message": (
                        f"'{key}' is set but '{metric}' is not in this "
                        f"enricher's {emits_key} — it has no effect. Add "
                        f"'{metric}' to {emits_key} to apply it."
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
